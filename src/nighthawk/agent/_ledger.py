"""The run ledger: host-owned, append-only, typed event record of one agent run.

Each appended event receives a stable event identity, a causal parent, and a
position in one authoritative total commit order. Interior execution reads the
ledger through deterministic queries and has no mutation surface.
"""

from __future__ import annotations

import dataclasses
import secrets
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from types import MappingProxyType, MemberDescriptorType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from nighthawk.runtime.step_definition import BlockKind
from nighthawk.runtime.step_outcome import OutcomeKind

from ._perimeter import EffectFootprint, PerimeterEffectClass, PerimeterPolicy

__all__ = [
    "CapabilityDescriptor",
    "CapabilityInvocationEvent",
    "DefinitionKind",
    "DefinitionMaterializationEvent",
    "DispatchEvent",
    "DispatchKind",
    "DispatchOutcomeEvent",
    "DispatchOutcomeStatus",
    "ExecutorStepObservation",
    "ExecutorUsage",
    "LedgerEntry",
    "NaturalExecutionOutcomeEvent",
    "PerimeterEffectAttemptEvent",
    "PerimeterEffectCompletionEvent",
    "ResourceExhaustionEvent",
    "RunEndEvent",
    "RunEntryEvent",
    "RunLedger",
    "RunLedgerEvent",
    "SourceProvenanceClass",
    "TerminationCompletionEvent",
    "TerminationRequestEvent",
    "TraceAnomalyEvent",
    "TraceAnomalyKind",
    "generate_ulid",
    "snapshot_value",
]


_CROCKFORD_BASE32_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_ulid() -> str:
    """Return a fresh ULID: 48 bits of millisecond time and 80 random bits, Crockford base32 encoded."""

    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    characters = []
    for _ in range(26):
        characters.append(_CROCKFORD_BASE32_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(characters))


LEDGER_SNAPSHOT_HOOK_NAME = "__nighthawk_ledger_snapshot__"
"""Method name a value may define to supply its own audit representation to :func:`snapshot_value`."""


def _is_frozen_record(value: object) -> bool:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return bool(value.__dataclass_params__.frozen)  # type: ignore[attr-defined]
    if isinstance(value, BaseModel):
        return bool(value.model_config.get("frozen", False))
    return False


def _record_attribute_name_to_value(value: object) -> dict[str, object]:
    """Every value a record instance holds: declared fields, extra fields, private attributes, and instance attributes."""

    attribute_name_to_value: dict[str, object] = {}
    if isinstance(value, BaseModel):
        attribute_name_to_value.update({name: getattr(value, name) for name in type(value).model_fields})
        attribute_name_to_value.update(value.model_extra or {})
        attribute_name_to_value.update(value.__pydantic_private__ or {})
    else:
        for field in dataclasses.fields(value):  # type: ignore[arg-type]
            attribute_name_to_value[field.name] = getattr(value, field.name)
    instance_dictionary = getattr(value, "__dict__", None)
    if isinstance(instance_dictionary, dict):
        attribute_name_to_value.update(instance_dictionary)
    for owner in type(value).__mro__:
        if owner is BaseModel:
            continue
        for name, descriptor in vars(owner).items():
            if isinstance(descriptor, MemberDescriptorType):
                with suppress(AttributeError):
                    attribute_name_to_value[name] = descriptor.__get__(value, type(value))
    return attribute_name_to_value


def _descriptor(value: object, *, kind: str = "object") -> str:
    return f"<{kind} {type(value).__module__}.{type(value).__qualname__}>"


@dataclasses.dataclass(frozen=True, slots=True)
class _MappingEntriesSnapshot:
    """Preserve every entry when projected keys cannot form a lossless mapping."""

    entry_tuple: tuple[tuple[object, object], ...]


def snapshot_value(value: object) -> object:
    """Return an immutable audit representation of ``value`` taken at commit time.

    Exact built-in primitives are kept; subclasses become their built-in scalar value unless they provide a snapshot hook.
    Sequences become tuples, sets become frozen sets, and mappings become read-only mapping proxies, each with snapshotted members.
    A set whose projected members collide becomes a tuple.
    A mapping whose projected keys collide or are unhashable becomes a frozen record with an ``entry_tuple`` of key/value pairs, preserving every observation.
    Frozen dataclasses and frozen Pydantic models are kept as typed records only when every value they hold
    (declared fields, extra fields, private attributes, slots, and instance attributes) snapshots to itself;
    otherwise they become read-only attribute mappings.
    A value that defines ``__nighthawk_ledger_snapshot__`` supplies its own representation (a generated callable reference supplies its definition identity).
    Any other callable or object is represented by a descriptor string, never by a live reference.
    Cycles, including through snapshot hooks, become explicit markers.
    Later ordinary mutations of committed values cannot rewrite the observation.

    The function is total: a value whose conversion raises is represented by a descriptor naming the failure,
    so recording an outcome never fails on the committed value.
    """

    try:
        return _snapshot_value(value, active_object_id_set=set())
    except Exception as error:  # noqa: BLE001 - The final fallback must preserve evidence for arbitrary application objects.
        return f"<snapshot failed {type(value).__module__}.{type(value).__qualname__}: {type(error).__name__}>"


def _snapshot_value(value: object, *, active_object_id_set: set[int]) -> object:
    if value is None or any(type(value) is primitive for primitive in (bool, int, float, str, bytes)):
        return value
    try:
        return _snapshot_structure(value, active_object_id_set=active_object_id_set)
    except Exception as error:  # noqa: BLE001 - Snapshot failures must remain evidence rather than abort execution.
        return f"<snapshot failed {type(value).__module__}.{type(value).__qualname__}: {type(error).__name__}>"


def _snapshot_structure(value: object, *, active_object_id_set: set[int]) -> object:
    object_id = id(value)
    if object_id in active_object_id_set:
        return "<cycle>"
    active_object_id_set.add(object_id)
    try:
        hook = getattr(type(value), LEDGER_SNAPSHOT_HOOK_NAME, None)
        if callable(hook):
            return _snapshot_value(hook(value), active_object_id_set=active_object_id_set)
        if isinstance(value, int):
            return int.__int__(value)
        if isinstance(value, float):
            return float.__float__(value)
        if isinstance(value, str):
            return str.__str__(value)
        if isinstance(value, bytes):
            return bytes.__bytes__(value)
        if isinstance(value, type) or callable(value) and not isinstance(value, Mapping | BaseModel) and not dataclasses.is_dataclass(value):
            qualified_name = getattr(value, "__qualname__", None) or type(value).__qualname__
            return f"<callable {getattr(value, '__module__', type(value).__module__)}.{qualified_name}>"
        if isinstance(value, BaseModel) or (dataclasses.is_dataclass(value) and not isinstance(value, type)):
            attribute_name_to_value = _record_attribute_name_to_value(value)
            attribute_name_to_snapshot = {
                name: _snapshot_value(item, active_object_id_set=active_object_id_set) for name, item in attribute_name_to_value.items()
            }
            if _is_frozen_record(value) and all(attribute_name_to_snapshot[name] is item for name, item in attribute_name_to_value.items()):
                return value
            return MappingProxyType(attribute_name_to_snapshot)
        if isinstance(value, Mapping):
            entry_tuple = tuple(
                (
                    _snapshot_value(key, active_object_id_set=active_object_id_set),
                    _snapshot_value(item, active_object_id_set=active_object_id_set),
                )
                for key, item in value.items()
            )
            try:
                key_to_snapshot = dict(entry_tuple)
            except TypeError:
                return _MappingEntriesSnapshot(entry_tuple)
            if len(key_to_snapshot) != len(entry_tuple):
                return _MappingEntriesSnapshot(entry_tuple)
            return MappingProxyType(key_to_snapshot)
        if isinstance(value, set | frozenset):
            item_snapshot_tuple = tuple(_snapshot_value(item, active_object_id_set=active_object_id_set) for item in value)
            try:
                item_snapshot_set = frozenset(item_snapshot_tuple)
            except TypeError:
                return item_snapshot_tuple
            return item_snapshot_set if len(item_snapshot_set) == len(item_snapshot_tuple) else item_snapshot_tuple
        if isinstance(value, list | tuple):
            return tuple(_snapshot_value(item, active_object_id_set=active_object_id_set) for item in value)
        return _descriptor(value)
    finally:
        active_object_id_set.discard(object_id)


class _FrozenEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class ExecutorUsage(_FrozenEvent):
    """Executor usage metered by the host for one Natural execution."""

    request_count: int = Field(default=0, ge=0)
    input_token_count: int = Field(default=0, ge=0)
    output_token_count: int = Field(default=0, ge=0)
    total_token_count: int = Field(default=0, ge=0)

    def __add__(self, other: ExecutorUsage) -> ExecutorUsage:
        return ExecutorUsage(
            request_count=self.request_count + other.request_count,
            input_token_count=self.input_token_count + other.input_token_count,
            output_token_count=self.output_token_count + other.output_token_count,
            total_token_count=self.total_token_count + other.total_token_count,
        )


class CapabilityDescriptor(_FrozenEvent):
    """Ledger projection of one capability in the authority ceiling."""

    name: str
    declared_type: str
    authority_boundary: str
    effect_footprint: EffectFootprint


type ExecutorStepObservation = Literal["model_requests", "executor_reports", "aggregate"]


class RunEntryEvent(_FrozenEvent):
    """Authority ceiling and opaque allocation admitted at run entry.

    ``executor_step_observation`` records how the host observes executor steps
    in this run (see ``nighthawk.agent.runtime.ExecutorStepObservation``); an
    ``aggregate`` run relies on the platform's executor for per-step
    prevention and the ledger shows that regime.
    """

    kind: Literal["run_entry"] = "run_entry"
    run_id: str
    input_type_name: str
    result_type_name: str
    capability_descriptor_tuple: tuple[CapabilityDescriptor, ...]
    reference_name_tuple: tuple[str, ...]
    composition_perimeter_policy: PerimeterPolicy
    platform_perimeter_policy: PerimeterPolicy
    effective_perimeter_policy: PerimeterPolicy
    resource_allocation_identity: str
    executor_step_observation: ExecutorStepObservation = "aggregate"


type SourceProvenanceClass = Literal["ratified", "generated"]
type DefinitionKind = Literal["function", "async_function", "value"]


class DefinitionMaterializationEvent(_FrozenEvent):
    """Introduction of one executable definition into the shared namespace."""

    kind: Literal["definition_materialization"] = "definition_materialization"
    definition_id: str
    export_name: str
    definition_kind: DefinitionKind
    source_provenance_class: SourceProvenanceClass
    defining_source_identity: str
    materialization_origin_node_id: str
    retained_definition_id: str | None = None
    approval_identity: str | None = None
    approval_route: str | None = None
    originating_cell_id: str | None = None


type DispatchKind = Literal["definition_only", "definition_and_invocation", "invocation"]


class DispatchEvent(_FrozenEvent):
    """Admission of one dispatch node, recorded before generated execution."""

    kind: Literal["dispatch"] = "dispatch"
    dispatch_node_id: str
    parent_node_id: str
    recursion_depth: int
    dispatch_kind: DispatchKind
    defining_source_identity: str
    definition_id: str | None
    export_name_tuple: tuple[str, ...]
    dependency_name_tuple: tuple[str, ...]
    effect_footprint: EffectFootprint
    grant_capability_name_tuple: tuple[str, ...]
    # None records in-process execution without an external confinement guarantee.
    confinement_identity: str | None
    # Invocation arguments captured after validation and before executing the body.
    # Materialization has no invocation arguments. Descriptors retain snapshot limits.
    argument_snapshot: object = None


type DispatchOutcomeStatus = Literal["success", "failure", "host_termination"]


class DispatchOutcomeEvent(_FrozenEvent):
    """The single terminal event of one dispatch node.

    ``result`` holds the :func:`snapshot_value` of the typed result committed by
    a successful invocation so that history queries over dispatch results are
    deterministic filters over the ledger; ``result_type_name`` names the
    committed value's type.
    """

    kind: Literal["dispatch_outcome"] = "dispatch_outcome"
    dispatch_node_id: str
    status: DispatchOutcomeStatus
    result: object = None
    result_type_name: str | None = None
    failure_class_name: str | None = None
    detail: str | None = None


class NaturalExecutionOutcomeEvent(_FrozenEvent):
    """The single terminal event of one Natural execution.

    ``block_kind`` distinguishes an ``execute`` block from a typed ``decide``
    judgment. ``outcome_kind`` is the committed Python control outcome of a
    successful execution.

    ``committed_binding_name_to_value`` is the :func:`snapshot_value` of the
    write set the core assigned into the containing Natural-function locals for
    a successful outcome, taken at terminal delivery; the mapping is read-only.
    ``executor_step_count`` and ``executor_usage`` are the steps and usage
    attributed to this execution alone; usage of nested Natural executions is
    attributed to their own outcome events.
    """

    # Actual provider response identities, in response observation order; None means unavailable.
    response_model_name_tuple: tuple[str | None, ...] = ()
    kind: Literal["natural_execution_outcome"] = "natural_execution_outcome"
    natural_execution_id: str
    natural_block_identity: str
    attribution_node_id: str
    block_kind: BlockKind
    status: Literal["success", "failure"]
    outcome_kind: OutcomeKind | None = None
    committed_binding_name_tuple: tuple[str, ...] = ()
    committed_binding_name_to_value: MappingProxyType[str, object] = Field(default_factory=lambda: MappingProxyType({}))
    failure_kind: str | None = None
    executor_step_count: int = Field(default=0, ge=0)
    executor_usage: ExecutorUsage = ExecutorUsage()


class CapabilityInvocationEvent(_FrozenEvent):
    """A typed capability request mediated by the host.

    ``result`` holds the :func:`snapshot_value` of the validated typed result
    committed for a successful request; it is ``None`` for failed and rejected
    requests.
    ``argument_snapshot`` captures validated arguments before entering the
    capability, including when its body fails or its result is rejected.
    Requests rejected before invocation have no admitted argument snapshot.
    """

    kind: Literal["capability_invocation"] = "capability_invocation"
    capability_name: str
    attribution_node_id: str
    status: Literal["success", "failure", "rejected"]
    argument_snapshot: object = None
    result: object = None
    result_type_name: str | None = None
    failure_class_name: str | None = None
    reason_code: str | None = None


class PerimeterEffectAttemptEvent(_FrozenEvent):
    """Recorded before a perimeter effect is attempted."""

    kind: Literal["perimeter_effect_attempt"] = "perimeter_effect_attempt"
    capability_name: str
    attribution_node_id: str
    perimeter_effect_class_tuple: tuple[PerimeterEffectClass, ...]


class PerimeterEffectCompletionEvent(_FrozenEvent):
    """Observed completion of a perimeter effect attempt."""

    kind: Literal["perimeter_effect_completion"] = "perimeter_effect_completion"
    attempt_event_id: str
    capability_name: str
    status: Literal["success", "failure"]
    failure_class_name: str | None = None


class ResourceExhaustionEvent(_FrozenEvent):
    """Platform resource allocation refused a request."""

    kind: Literal["resource_exhaustion"] = "resource_exhaustion"
    request_kind: Literal["dispatch", "natural_execution", "executor_step"]
    attribution_node_id: str
    recursion_depth: int
    reason: str


type TraceAnomalyKind = Literal[
    "grant_projection_mismatch",
    "ambient_tool_visible",
    "oversight_hook_active",
    "lifecycle_hook_replaced",
    "commit_hook_skipped",
    "definition_code_modified",
    "namespace_guard_modified",
    "expression_raw_dynamic_execution",
    "operation_under_gateway_allowance",
    "unmediated_generated_execution",
    "unmediated_perimeter_effect",
    "interior_branch_active_at_run_end",
    "branch_cessation_unestablished",
]


class TraceAnomalyEvent(_FrozenEvent):
    """Detected inconsistency between observed run state and ledger-derivable state."""

    kind: Literal["trace_anomaly"] = "trace_anomaly"
    anomaly_kind: TraceAnomalyKind
    attribution_node_id: str
    detail: str


class TerminationRequestEvent(_FrozenEvent):
    """A parent requested forced termination of a descendant branch."""

    kind: Literal["termination_request"] = "termination_request"
    requesting_node_id: str
    target_node_id: str
    affected_node_id_tuple: tuple[str, ...]


class TerminationCompletionEvent(_FrozenEvent):
    """Whether the platform established cessation of the targeted branches.

    For host-directed run termination ``affected_node_id_tuple`` includes the
    run root while its ratified interior execution is still active.
    """

    kind: Literal["termination_completion"] = "termination_completion"
    request_event_id: str
    target_node_id: str
    affected_node_id_tuple: tuple[str, ...]
    ceased: bool


class RunEndEvent(_FrozenEvent):
    """The run-end host seam decision.

    ``result`` holds the :func:`snapshot_value` of the accepted typed run
    result; it is ``None`` for every other terminal kind.
    """

    kind: Literal["run_end"] = "run_end"
    run_id: str
    terminal_kind: Literal["accepted", "validation_rejection", "uncaught_exception", "host_termination"]
    result: object = None
    result_type_name: str | None = None
    failure_class_name: str | None = None
    detail: str | None = None


type RunLedgerEvent = Annotated[
    RunEntryEvent
    | DefinitionMaterializationEvent
    | DispatchEvent
    | DispatchOutcomeEvent
    | NaturalExecutionOutcomeEvent
    | CapabilityInvocationEvent
    | PerimeterEffectAttemptEvent
    | PerimeterEffectCompletionEvent
    | ResourceExhaustionEvent
    | TraceAnomalyEvent
    | TerminationRequestEvent
    | TerminationCompletionEvent
    | RunEndEvent,
    Field(discriminator="kind"),
]


class LedgerEntry(_FrozenEvent):
    """One committed run-ledger event with identity, order, and causal parent."""

    event_id: str
    sequence: int = Field(ge=0)
    parent_event_id: str | None
    event: RunLedgerEvent


class RunLedger:
    """Append-only typed event record of one agent run.

    Only the host appends. ``entries`` is the authoritative total commit order;
    query helpers are deterministic filters over it.
    """

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._lock = threading.Lock()
        self._entry_list: list[LedgerEntry] = []
        self._event_id_to_entry: dict[str, LedgerEntry] = {}

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        with self._lock:
            return tuple(self._entry_list)

    def reserve_event_id(self) -> str:
        """Return a fresh event identity for an event recorded elsewhere first."""

        return generate_ulid()

    def _append(
        self,
        event: RunEntryEvent
        | DefinitionMaterializationEvent
        | DispatchEvent
        | DispatchOutcomeEvent
        | NaturalExecutionOutcomeEvent
        | CapabilityInvocationEvent
        | PerimeterEffectAttemptEvent
        | PerimeterEffectCompletionEvent
        | ResourceExhaustionEvent
        | TraceAnomalyEvent
        | TerminationRequestEvent
        | TerminationCompletionEvent
        | RunEndEvent,
        *,
        parent_event_id: str | None = None,
        event_id: str | None = None,
    ) -> LedgerEntry:
        with self._lock:
            entry = LedgerEntry(
                event_id=event_id if event_id is not None else generate_ulid(),
                sequence=len(self._entry_list),
                parent_event_id=parent_event_id,
                event=event,
            )
            self._entry_list.append(entry)
            self._event_id_to_entry[entry.event_id] = entry
            return entry

    def find(self, event_id: str) -> LedgerEntry | None:
        with self._lock:
            return self._event_id_to_entry.get(event_id)

    def select[E: BaseModel](self, event_type: type[E]) -> tuple[LedgerEntry, ...]:
        """Return entries whose event is an instance of ``event_type``, in commit order."""

        return tuple(entry for entry in self.entries if isinstance(entry.event, event_type))

    def dispatch_outcome(self, dispatch_node_id: str) -> LedgerEntry | None:
        for entry in self.entries:
            if isinstance(entry.event, DispatchOutcomeEvent) and entry.event.dispatch_node_id == dispatch_node_id:
                return entry
        return None

    def dispatch_status(self, dispatch_node_id: str) -> Literal["nonterminal", "success", "failure", "host_termination"] | None:
        """Derive a dispatch node's status from its dispatch and outcome events only."""

        admitted = any(isinstance(entry.event, DispatchEvent) and entry.event.dispatch_node_id == dispatch_node_id for entry in self.entries)
        if not admitted:
            return None
        outcome = self.dispatch_outcome(dispatch_node_id)
        if outcome is None:
            return "nonterminal"
        assert isinstance(outcome.event, DispatchOutcomeEvent)
        return outcome.event.status

    def definition(self, definition_id: str) -> LedgerEntry | None:
        for entry in self.entries:
            if isinstance(entry.event, DefinitionMaterializationEvent) and entry.event.definition_id == definition_id:
                return entry
        return None

    def dispatch_count(self) -> int:
        """Number of admitted dispatch nodes; the run root is not a dispatch."""

        return len(self.select(DispatchEvent))

    def executor_step_count(self, attribution_node_id: str | None = None) -> int:
        """Executor steps metered for one attribution node, or across the whole attribution tree."""

        return sum(
            entry.event.executor_step_count
            for entry in self.entries
            if isinstance(entry.event, NaturalExecutionOutcomeEvent)
            and (attribution_node_id is None or entry.event.attribution_node_id == attribution_node_id)
        )

    def attribution_node_id_to_executor_usage(self) -> dict[str, ExecutorUsage]:
        """Deterministic per-node attribution of metered executor usage."""

        node_id_to_usage: dict[str, ExecutorUsage] = {}
        for entry in self.entries:
            if isinstance(entry.event, NaturalExecutionOutcomeEvent):
                node_id = entry.event.attribution_node_id
                node_id_to_usage[node_id] = node_id_to_usage.get(node_id, ExecutorUsage()) + entry.event.executor_usage
        return node_id_to_usage

    def __len__(self) -> int:
        with self._lock:
            return len(self._entry_list)
