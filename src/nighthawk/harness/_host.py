"""Host control plane of one agent run; ``dispatch`` and ``_run`` compose it for generated source and for run entry and end."""

from __future__ import annotations

import ast
import asyncio
import sys
import threading
import typing
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast

import nighthawk as nh
from nighthawk._identifiers import generate_ulid
from nighthawk.adapters.pydantic_ai.scoping import get_model_scope
from nighthawk.core.lifecycle import FailureStage, StepCompleted, StepFinished, StepInterrupted, StepLifecycle, StepRaised
from nighthawk.core.operation_contract import OperationRequest
from nighthawk.core.oversight import Accept, Oversight, RejectOperation, StepCommit, StepCommitDecision
from nighthawk.core.scoping import get_scope
from nighthawk.core.step_definition import BlockKind
from nighthawk.core.step_outcome import OutcomeKind, StepOutcome
from nighthawk.core.step_request import StepRequest
from nighthawk.core.usage import ExecutionUsage, UsageMeter
from nighthawk.harness._audit import gateway_allowance_active, host_service_scope, observe_run, permit_effects
from nighthawk.harness._capability_execution import CAPABILITY_ATTRIBUTE_NAME, HOST_ATTRIBUTE_NAME, build_capability_wrapper
from nighthawk.harness._observability import emit_harness_event
from nighthawk.harness._source import detect_raw_dynamic_execution
from nighthawk.harness.context import (
    AttributionNode,
    ExecutionContext,
    _active_natural_execution_var,
    _current_host_var,
    _execution_context_var,
)
from nighthawk.harness.errors import (
    CapabilityRejectedError,
    DispatchRejectedError,
    NaturalExecutionError,
    NaturalExecutionFailureKind,
    RunTerminalKind,
    TerminationError,
)
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import (
    DispatchOutcomeEvent,
    DispatchOutcomeStatus,
    ExecutorStepObservation,
    NaturalExecutionOutcomeEvent,
    ResourceExhaustionEvent,
    RunEndEvent,
    RunLedger,
    TerminationCompletionEvent,
    TerminationRequestEvent,
    TraceAnomalyEvent,
    TraceAnomalyKind,
    snapshot_value,
)
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import ResourceAllocation, ResourceConsumption, ResourceRejection, ResourceRequest, ResourceRequestKind
from nighthawk.harness.runner import Runner
from nighthawk.harness.termination import TerminationCompletion, TerminationReport, TerminationRequest

if typing.TYPE_CHECKING:
    from nighthawk.harness.dispatch import GeneratedCallableReference

__all__ = [
    "Host",
]

GENERATED_MODULE_NAME_PREFIX = "nighthawk_generated_"
"""``__name__`` prefix of every generated namespace; Natural blocks defined there report it in their block identity."""


_FAILURE_STAGE_TO_FAILURE_KIND: dict[FailureStage, NaturalExecutionFailureKind] = {
    "executor": "executor_error",
    "outcome_validation": "disallowed_outcome_kind",
    "binding_validation": "write_set_invalid",
    "commit_inspection": "commit_failed",
    "oversight_abort": "commit_failed",
    "rewrite_validation": "commit_failed",
    "binding_assignment": "commit_failed",
}
"""Core failure stages the host owns a failure kind for; every other stage is a ``core_failure``."""

HOST_OWNED_ATTRIBUTE_NAME = "__nighthawk_host_owned__"
"""Attribute marking Pydantic AI capabilities the host itself installs around a Natural execution."""


@dataclass
class _NaturalExecutionRecord:
    """Host-side state of one admitted Natural execution until its outcome event is recorded.

    ``step_execution_id`` is the core's invocation identity, shared by the
    executor call, the commit inspection, and the terminal delivery, so the
    host correlates all three to this record. ``meter`` is the usage meter installed for this execution alone, so usage
    the executor reports is attributed to it and not to any concurrent or
    enclosing execution. ``observed_usage`` accumulates the LLM responses the
    host observed one by one and ``reported_usage`` what it already reported
    to the allocation. ``admitted_step_count`` counts executor steps presented
    for admission and ``observed_step_count`` counts observed LLM responses.
    """

    natural_execution_id: str
    natural_block_identity: str
    block_kind: BlockKind
    step_execution_id: str
    node: AttributionNode
    meter: UsageMeter
    response_model_name_tuple: tuple[str | None, ...] = ()
    observed_usage: ExecutionUsage = field(default_factory=ExecutionUsage)
    reported_usage: ExecutionUsage = field(default_factory=ExecutionUsage)
    own_usage: ExecutionUsage | None = None
    admitted_step_count: int = 0
    observed_step_count: int = 0
    executor_step_count: int = 0
    outcome_event_id: str | None = None
    failure_kind: NaturalExecutionFailureKind | None = None


class Host:
    """Host control plane state and mechanisms of one agent run."""

    def __init__(
        self,
        *,
        run_id: str,
        runner: Runner,
        ledger: RunLedger,
        effective_perimeter_policy: PerimeterPolicy,
        resource_allocation: ResourceAllocation,
        function_definition_tuple: tuple[Callable[..., object], ...] = (),
    ) -> None:
        self.run_id = run_id
        self.runner = runner
        self.ledger = ledger
        self.effective_perimeter_policy = effective_perimeter_policy
        self.resource_allocation = resource_allocation
        self._function_definition_tuple = function_definition_tuple
        self.run_root = AttributionNode(node_id=generate_ulid(), parent_node_id=None, recursion_depth=0, kind="run_root")
        self._lock = threading.RLock()
        self._node_id_to_node: dict[str, AttributionNode] = {self.run_root.node_id: self.run_root}
        self._node_id_to_anchor_event_id: dict[str, str] = {}
        self._nonterminal_dispatch_node_id_set: set[str] = set()
        self._node_id_to_owning_loop: dict[str, asyncio.AbstractEventLoop] = {}
        self._terminated_node_id_set: set[str] = set()
        self._node_id_to_natural_execution_id_to_record: dict[str, dict[str, _NaturalExecutionRecord]] = {}
        self._capability_to_wrapper: dict[Capability, Callable[..., object]] = {}
        self._retained_function_to_reference: dict[object, GeneratedCallableReference] = {}
        self._ended = False
        self._terminated = False
        self._run_root_active = False
        self._run_end_event_id: str | None = None
        self._pending_failure_class_name: str | None = None
        self._run_usage_meter: UsageMeter | None = None
        self.executor_step_observation: ExecutorStepObservation = "aggregate"
        self._oversight = Oversight(inspect_operation=self._inspect_operation, inspect_step_commit=self._inspect_step_commit)
        self._lifecycle = StepLifecycle(on_step_finished=self._on_step_finished)
        self._step_execution_id_to_record: dict[str, _NaturalExecutionRecord] = {}

    def has_function_definition(self, function: Callable[..., object]) -> bool:
        """Match the exact repository/approved callable supplied at run entry.

        This is host-owned source identity evidence, not an additional grant.
        Integrations can recognize shared Natural parts without trusting a
        module name or allowing a producing run to register its own approval.
        """
        return any(function is definition for definition in self._function_definition_tuple)

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def terminated(self) -> bool:
        return self._terminated

    @property
    def run_root_active(self) -> bool:
        """Whether the scaffold composition's ratified interior execution is still running."""

        return self._run_root_active

    @property
    def run_end_event_id(self) -> str | None:
        return self._run_end_event_id

    def mark_ended(self) -> None:
        """Close interior admission; this alone does not establish cessation."""

        with self._lock:
            self._ended = True
            self._retained_function_to_reference.clear()

    def retain_pending_run_failure(self, failure: BaseException | None) -> None:
        """Retain terminal failure evidence without keeping exception frames."""

        with self._lock:
            if failure is not None and self._pending_failure_class_name is None:
                self._pending_failure_class_name = type(failure).__name__

    def record_run_end(
        self,
        terminal_kind: Literal["accepted"] | RunTerminalKind,
        *,
        result: object = None,
        result_type_name: str | None = None,
        failure_class_name: str | None = None,
        detail: str | None = None,
    ) -> tuple[str, bool]:
        """Append the single run-end event and mark the run ended.

        Returns the run-end event identity and whether this call recorded it.
        A run already ended keeps its existing run-end event.
        """

        with self._lock:
            if self._run_end_event_id is not None:
                return self._run_end_event_id, False
            entry = self.ledger._append(
                RunEndEvent(
                    run_id=self.run_id,
                    terminal_kind=terminal_kind,
                    result=snapshot_value(result),
                    result_type_name=result_type_name,
                    failure_class_name=failure_class_name or self._pending_failure_class_name,
                    detail=detail,
                ),
                parent_event_id=self.anchor_event_id(self.run_root.node_id),
            )
            self._run_end_event_id = entry.event_id
            self._ended = True
            self._retained_function_to_reference.clear()
        emit_harness_event("run_end", {"run_id": self.run_id, "terminal_kind": terminal_kind})
        return entry.event_id, True

    def node(self, node_id: str) -> AttributionNode | None:
        with self._lock:
            return self._node_id_to_node.get(node_id)

    def is_node_terminated(self, node_id: str) -> bool:
        with self._lock:
            return node_id in self._terminated_node_id_set

    def is_node_live(self, node: AttributionNode) -> bool:
        """Whether ``node`` may still host interior execution: a nonterminal dispatch node, or the run root while the scaffold executes."""

        with self._lock:
            if node.kind == "dispatch":
                return node.node_id in self._nonterminal_dispatch_node_id_set
            return self._run_root_active and not self._ended

    def nonterminal_dispatch_node_id_tuple(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._nonterminal_dispatch_node_id_set))

    def register_branch_loop(self, node: AttributionNode) -> None:
        """Remember the loop of an observed async dispatch until its outcome."""

        with self._lock:
            self._node_id_to_owning_loop[node.node_id] = asyncio.get_running_loop()

    def pending_branch_loop_tuple(self) -> tuple[asyncio.AbstractEventLoop, ...]:
        with self._lock:
            return tuple(set(self._node_id_to_owning_loop.values()))

    def anchor_event_id(self, node_id: str) -> str | None:
        """Return the event that admitted ``node_id``: run entry for the root, dispatch for a dispatch node."""

        with self._lock:
            return self._node_id_to_anchor_event_id.get(node_id)

    def set_anchor_event_id(self, node_id: str, event_id: str) -> None:
        with self._lock:
            self._node_id_to_anchor_event_id[node_id] = event_id

    def record_anomaly(self, anomaly_kind: TraceAnomalyKind, *, node_id: str, detail: str) -> str:
        entry = self.ledger._append(
            TraceAnomalyEvent(anomaly_kind=anomaly_kind, attribution_node_id=node_id, detail=detail),
            parent_event_id=self.anchor_event_id(node_id),
        )
        emit_harness_event("trace_anomaly", {"anomaly_kind": anomaly_kind, "node_id": node_id})
        return entry.event_id

    @contextmanager
    def run_scope(self, grant: Grant) -> Generator[None]:
        """Run-root context: the scaffold composition's ratified interior execution runs inside."""

        host_token = _current_host_var.set(self)
        with self._lock:
            self._run_root_active = True
            self._run_usage_meter = get_scope().usage_meter
        try:
            with observe_run(self), self.execution_scope(grant, self.run_root):
                yield
        finally:
            with self._lock:
                self._run_root_active = False
            _current_host_var.reset(host_token)

    @contextmanager
    def execution_scope(self, grant: Grant, node: AttributionNode, *, generated_source: object | None = None) -> Generator[None]:
        """Establish the grant, attribution node, and generated source in effect and project the grant into the core scope.

        The core scope is composed so that the prompt-facing surface is exactly
        the grant: implicit references are the grant's capability wrappers, core
        tools and Pydantic AI capabilities declared by enclosing scopes are
        cleared, the core step-commit inspector is the host's pre-commit seam
        (:meth:`_inspect_step_commit`), and the core step lifecycle is the
        host's terminal seam (:meth:`_on_step_finished`), which records exactly
        one outcome event per Natural execution after the core finished it.
        """

        context_token = _execution_context_var.set(ExecutionContext(grant=grant, node=node, generated_source=generated_source))
        try:
            with (
                permit_effects(frozenset()),
                nh.scope(task_references=self.projection(grant), oversight=self._oversight, lifecycle=self._lifecycle, tools=(), capabilities=()),
            ):
                yield
        finally:
            _execution_context_var.reset(context_token)

    def current_context(self) -> ExecutionContext | None:
        if _current_host_var.get() is not self:
            return None
        return _execution_context_var.get()

    def projection(self, grant: Grant) -> dict[str, Callable[..., object]]:
        """Prompt-facing surface of a grant: capability wrappers by name."""

        return {name: self.capability_wrapper(capability) for name, capability in grant.capabilities.items()}

    def require_generated_execution(self, source: object) -> None:
        """Refuse generated code of ``source`` that starts or resumes outside a live generated execution of its own dispatch.

        Called by the guard compiled into every generated function body,
        handler, and proxy resumption. Passing requires this host to be current,
        the run to be live, the current attribution node to be a dispatch node
        that is neither terminated nor terminal (no dispatch outcome recorded
        yet), and that node's generated execution to run ``source``. A context
        copied out of a finished dispatch therefore no longer admits its code.

        When the refusal happens while a ``GeneratorExit`` is being handled (the
        interpreter is finalizing a leaked generator or coroutine), that
        ``GeneratorExit`` is re-raised instead so the object finalizes without
        running its generated handlers and without an unraisable exception.

        Raises:
            DispatchRejectedError: with ``unmediated_execution`` (recorded as a
                trace anomaly while the run is live), ``run_ended``,
                ``run_terminated``, or ``node_terminated``.
        """

        rejection = self._generated_execution_rejection(source)
        if rejection is None:
            return
        active_exception = sys.exception()
        if isinstance(active_exception, GeneratorExit):
            raise active_exception
        raise rejection

    def _generated_execution_rejection(self, source: object) -> DispatchRejectedError | None:
        if self._ended:
            return DispatchRejectedError(reason="run_ended")
        context = self.current_context()
        if context is None or context.node.kind != "dispatch" or context.generated_source is not source:
            node_id = context.node.node_id if context is not None else self.run_root.node_id
            anomaly_event_id = self.record_anomaly(
                "unmediated_generated_execution",
                node_id=node_id,
                detail="generated code started or resumed outside a generated execution of its dispatch",
            )
            return DispatchRejectedError(reason="unmediated_execution", attribution_node_id=node_id, rejection_event_id=anomaly_event_id)
        if self._terminated:
            return DispatchRejectedError(reason="run_terminated", attribution_node_id=context.node.node_id)
        if self.is_node_terminated(context.node.node_id):
            return DispatchRejectedError(reason="node_terminated", attribution_node_id=context.node.node_id)
        if not self.is_node_live(context.node):
            anomaly_event_id = self.record_anomaly(
                "unmediated_generated_execution",
                node_id=context.node.node_id,
                detail="generated code started or resumed after its dispatch node recorded its outcome",
            )
            return DispatchRejectedError(reason="unmediated_execution", attribution_node_id=context.node.node_id, rejection_event_id=anomaly_event_id)
        return None

    def admit_resource(self, kind: ResourceRequestKind, node: AttributionNode) -> ResourceRejection | None:
        with host_service_scope():
            return self.resource_allocation.admit(
                ResourceRequest(run_id=self.run_id, kind=kind, attribution_node_id=node.node_id, recursion_depth=node.recursion_depth)
            )

    def record_exhaustion(self, kind: ResourceRequestKind, node: AttributionNode, rejection: ResourceRejection) -> str:
        entry = self.ledger._append(
            ResourceExhaustionEvent(
                request_kind=kind,
                attribution_node_id=node.node_id,
                recursion_depth=node.recursion_depth,
                reason=rejection.reason,
            ),
            parent_event_id=self.anchor_event_id(node.node_id if node.node_id else node.parent_node_id or ""),
        )
        emit_harness_event("resource_exhaustion", {"request_kind": kind, "node_id": node.node_id, "reason": rejection.reason})
        return entry.event_id

    def record_consumption(
        self,
        kind: ResourceRequestKind,
        node: AttributionNode,
        *,
        executor_step_count: int = 0,
        executor_usage: ExecutionUsage | None = None,
    ) -> None:
        with host_service_scope():
            self.resource_allocation.record(
                ResourceConsumption(
                    run_id=self.run_id,
                    kind=kind,
                    attribution_node_id=node.node_id,
                    recursion_depth=node.recursion_depth,
                    executor_step_count=executor_step_count,
                    executor_usage=executor_usage if executor_usage is not None else ExecutionUsage(),
                )
            )

    def create_dispatch_node(self, parent: AttributionNode) -> AttributionNode:
        node = AttributionNode(
            node_id=generate_ulid(),
            parent_node_id=parent.node_id,
            recursion_depth=parent.recursion_depth + 1,
            kind="dispatch",
        )
        with self._lock:
            self._node_id_to_node[node.node_id] = node
            self._nonterminal_dispatch_node_id_set.add(node.node_id)
        return node

    def record_dispatch_outcome(
        self,
        node: AttributionNode,
        status: DispatchOutcomeStatus,
        *,
        result: object = None,
        result_type_name: str | None = None,
        failure_class_name: str | None = None,
        detail: str | None = None,
        parent_event_id: str | None = None,
    ) -> str | None:
        """Append the single dispatch-outcome event of ``node``; no-op once terminal."""

        with self._lock:
            if node.node_id not in self._nonterminal_dispatch_node_id_set:
                return None
            self._nonterminal_dispatch_node_id_set.discard(node.node_id)
            self._node_id_to_owning_loop.pop(node.node_id, None)
            entry = self.ledger._append(
                DispatchOutcomeEvent(
                    dispatch_node_id=node.node_id,
                    status=status,
                    result=snapshot_value(result),
                    result_type_name=result_type_name,
                    failure_class_name=failure_class_name,
                    detail=detail,
                ),
                parent_event_id=parent_event_id if parent_event_id is not None else self.anchor_event_id(node.node_id),
            )
        emit_harness_event("dispatch_outcome", {"node_id": node.node_id, "status": status})
        return entry.event_id

    def _descendant_node_id_tuple(self, root_node_id: str) -> tuple[str, ...]:
        with self._lock:
            descendants: list[str] = []
            frontier = [root_node_id]
            while frontier:
                current = frontier.pop()
                for node in self._node_id_to_node.values():
                    if node.parent_node_id == current:
                        descendants.append(node.node_id)
                        frontier.append(node.node_id)
            return tuple(descendants)

    def _is_descendant(self, node_id: str, ancestor_node_id: str) -> bool:
        with self._lock:
            current = self._node_id_to_node.get(node_id)
            while current is not None and current.parent_node_id is not None:
                if current.parent_node_id == ancestor_node_id:
                    return True
                current = self._node_id_to_node.get(current.parent_node_id)
            return False

    def begin_natural_execution(self, request: StepRequest) -> _NaturalExecutionRecord:
        """Admit one Natural execution at its mediation seam.

        The execution is rejected when the run or its attribution node is
        terminated, when a generated Natural block runs outside a generated
        execution of its dispatch, when anything outside the grant would be
        visible to the prompt (undeclared implicit references, or core tools and
        Pydantic AI capabilities declared outside the host projection), when a core oversight hook
        would commit or reject outside host mediation, or when the resource
        allocation refuses it.

        Raises:
            NaturalExecutionError: after recording the failed outcome event.
        """

        context = self.current_context()
        if context is None:
            raise CapabilityRejectedError(capability_name="natural_execution", reason_code="no_active_run")
        step_context = request.step_context
        execution_reference = step_context.execution_reference
        record = _NaturalExecutionRecord(
            natural_execution_id=generate_ulid(),
            natural_block_identity=execution_reference.source_location or "<unknown>",
            block_kind=request.block_kind,
            step_execution_id=execution_reference.step_execution_id or generate_ulid(),
            node=context.node,
            meter=UsageMeter(),
        )
        # Registered before any admission check so that a rejection raised here
        # is recognized as this execution's own outcome at terminal delivery.
        with self._lock:
            self._step_execution_id_to_record[record.step_execution_id] = record
        if self._terminated or self._ended:
            raise self.fail_natural_execution(record, "terminated", "the run is terminated")
        if self.is_node_terminated(context.node.node_id):
            raise self.fail_natural_execution(record, "terminated", "the attribution node is terminated")
        if not self.is_node_live(context.node):
            anomaly_event_id = self.record_anomaly(
                "unmediated_generated_execution",
                node_id=context.node.node_id,
                detail=f"Natural block {record.natural_block_identity} executed after its attribution node ended",
            )
            raise self.fail_natural_execution(
                record,
                "mediation_bypass",
                "the attribution node already recorded its outcome",
                parent_event_id=anomaly_event_id,
            )
        block_module_name = record.natural_block_identity.rsplit(":", 1)[0]
        if block_module_name.startswith(GENERATED_MODULE_NAME_PREFIX) and (context.node.kind != "dispatch" or context.generated_source is None):
            anomaly_event_id = self.record_anomaly(
                "unmediated_generated_execution",
                node_id=context.node.node_id,
                detail=f"generated Natural block {record.natural_block_identity} executed outside a generated execution",
            )
            raise self.fail_natural_execution(
                record,
                "mediation_bypass",
                "a generated Natural block executed outside a generated execution of its dispatch",
                parent_event_id=anomaly_event_id,
            )

        expected_projection = self.projection(context.grant)
        unexpected_name_list = [
            name
            for name, value in step_context.environment.task_name_to_value.items()
            if not isinstance(value, typing.TypeAliasType) and expected_projection.get(name) is not value
        ]
        if unexpected_name_list:
            anomaly_event_id = self.record_anomaly(
                "grant_projection_mismatch",
                node_id=context.node.node_id,
                detail=f"undeclared implicit references: {', '.join(sorted(unexpected_name_list))}",
            )
            raise self.fail_natural_execution(
                record,
                "grant_violation",
                "implicit references outside the declared grant",
                parent_event_id=anomaly_event_id,
            )
        ambient_name_list = sorted(tool.name for tool in get_model_scope().tools)
        ambient_name_list.extend(
            sorted(
                capability.id or type(capability).__qualname__
                for capability in get_model_scope().capabilities
                if not getattr(capability, HOST_OWNED_ATTRIBUTE_NAME, False)
            )
        )
        if ambient_name_list:
            anomaly_event_id = self.record_anomaly(
                "ambient_tool_visible",
                node_id=context.node.node_id,
                detail=f"tools or capabilities declared outside the grant: {', '.join(ambient_name_list)}",
            )
            raise self.fail_natural_execution(
                record,
                "grant_violation",
                "core tools or Pydantic AI capabilities declared outside the grant are visible to the execution",
                parent_event_id=anomaly_event_id,
            )
        if get_scope().lifecycle is not self._lifecycle:
            # Without the host lifecycle the core wraps this rejection in its own
            # ExecutionError; the anomaly and the failed outcome still identify it.
            anomaly_event_id = self.record_anomaly(
                "lifecycle_hook_replaced",
                node_id=context.node.node_id,
                detail="the host step lifecycle was cleared or replaced around the Natural execution",
            )
            raise self.fail_natural_execution(
                record,
                "mediation_bypass",
                "the core terminal delivery seam is not the host's",
                parent_event_id=anomaly_event_id,
            )
        installed_oversight = get_scope().oversight
        if installed_oversight is not self._oversight:
            detail = (
                "the host commit inspector was cleared around the Natural execution"
                if installed_oversight is None
                else "a core oversight hook replaced the host commit inspector around the Natural execution"
            )
            anomaly_event_id = self.record_anomaly("oversight_hook_active", node_id=context.node.node_id, detail=detail)
            raise self.fail_natural_execution(
                record,
                "mediation_bypass",
                "the core commit inspection seam is not the host's",
                parent_event_id=anomaly_event_id,
            )

        rejection = self.admit_resource("natural_execution", context.node)
        if rejection is not None:
            exhaustion_event_id = self.record_exhaustion("natural_execution", context.node, rejection)
            raise self.fail_natural_execution(record, "allocation_exhausted", rejection.reason, parent_event_id=exhaustion_event_id)

        with self._lock:
            self._node_id_to_natural_execution_id_to_record.setdefault(context.node.node_id, {})[record.natural_execution_id] = record
        return record

    @contextmanager
    def natural_execution_context(self, record: _NaturalExecutionRecord) -> Generator[None]:
        """Run the executor steps of ``record`` with their own usage meter and step admission, without any gateway allowance.

        The core usage meter of the context is replaced by the record's meter so
        that whatever the executor reports is attributed to this execution
        alone, even when executions run concurrently or nest; the run-level
        meter receives the execution's usage when its outcome is recorded.
        A Natural function called inside a perimeter capability or another host gateway
        therefore evaluates its operations without that gateway's effects;
        each admitted provider request re-enters host infrastructure on its own.
        """

        record_token = _active_natural_execution_var.set(record)
        try:
            with permit_effects(frozenset()), nh.scope(usage_meter=record.meter):
                yield
        finally:
            _active_natural_execution_var.reset(record_token)

    def admit_executor_step(self, record: _NaturalExecutionRecord) -> None:
        """Present one executor step for admission before its LLM request.

        Raises:
            NaturalExecutionError: after recording the exhaustion and the failed
                outcome event, when the allocation refuses the step.
        """

        if record.outcome_event_id is not None:
            raise self._error_of_record(record, "the Natural execution already has an outcome")
        rejection = self.admit_resource("executor_step", record.node)
        if rejection is not None:
            exhaustion_event_id = self.record_exhaustion("executor_step", record.node, rejection)
            raise self.fail_natural_execution(record, "allocation_exhausted", rejection.reason, parent_event_id=exhaustion_event_id)
        record.admitted_step_count += 1

    def observe_executor_step(self, record: _NaturalExecutionRecord, usage: ExecutionUsage | None = None, *, model_name: str | None = None) -> None:
        """Account one observed LLM response immediately so the next step admission sees its usage."""

        observed = usage if usage is not None else ExecutionUsage(request_count=1)
        record.response_model_name_tuple += (model_name,)
        record.observed_step_count += 1
        record.observed_usage = record.observed_usage + observed
        record.reported_usage = record.reported_usage + observed
        self.record_consumption("executor_step", record.node, executor_step_count=1, executor_usage=observed)

    def _error_of_record(self, record: _NaturalExecutionRecord, message: str) -> NaturalExecutionError:
        assert record.outcome_event_id is not None
        return NaturalExecutionError(
            natural_execution_id=record.natural_execution_id,
            outcome_event_id=record.outcome_event_id,
            attribution_node_id=record.node.node_id,
            failure_kind=record.failure_kind if record.failure_kind is not None else "terminated",
            message=message,
        )

    def _unregister_natural_execution(self, record: _NaturalExecutionRecord) -> None:
        with self._lock:
            natural_execution_id_to_record = self._node_id_to_natural_execution_id_to_record.get(record.node.node_id)
            if natural_execution_id_to_record is not None:
                natural_execution_id_to_record.pop(record.natural_execution_id, None)

    def _own_usage(self, record: _NaturalExecutionRecord) -> ExecutionUsage:
        """Usage attributed to ``record``: what the executor reported to the record's meter, or the observed responses when it reported nothing.

        Computed once per record and forwarded to the run-level meter so that the
        core usage view of the scaffold composition stays complete.
        """

        with self._lock:
            if record.own_usage is not None:
                return record.own_usage
            metered = record.meter.snapshot()
            record.own_usage = metered if metered.request_count > 0 or metered.total_token_count > 0 else record.observed_usage
            if self._run_usage_meter is not None and (record.own_usage.request_count or record.own_usage.total_token_count):
                self._run_usage_meter.record(record.own_usage)
            return record.own_usage

    def _settle_executor_steps(self, record: _NaturalExecutionRecord, own_usage: ExecutionUsage) -> str | None:
        """Present executor steps the host learned of only from metered usage for admission.

        Returns the exhaustion event identity when the allocation refused one of
        those steps; the caller decides whether that fails the execution.
        """

        step_count = max(record.observed_step_count, own_usage.request_count)
        while record.admitted_step_count < step_count:
            rejection = self.admit_resource("executor_step", record.node)
            if rejection is not None:
                return self.record_exhaustion("executor_step", record.node, rejection)
            record.admitted_step_count += 1
        return None

    def _report_remaining_consumption(self, record: _NaturalExecutionRecord, own_usage: ExecutionUsage) -> None:
        remaining = own_usage.difference(record.reported_usage)
        if remaining.request_count or remaining.total_token_count or record.admitted_step_count:
            self.record_consumption("natural_execution", record.node, executor_step_count=record.admitted_step_count, executor_usage=remaining)
        record.reported_usage = record.reported_usage + remaining

    def fail_natural_execution(
        self,
        record: _NaturalExecutionRecord,
        failure_kind: NaturalExecutionFailureKind,
        message: str,
        *,
        raise_error_type_name: str | None = None,
        parent_event_id: str | None = None,
    ) -> NaturalExecutionError:
        """Record the failed outcome event once and return the typed exception.

        A record that already has an outcome keeps its first failure kind; the
        returned exception then identifies that recorded outcome.
        """

        with self._lock:
            already_recorded = record.outcome_event_id is not None
        if already_recorded:
            return self._error_of_record(record, message)
        usage = self._own_usage(record)
        exhaustion_event_id = self._settle_executor_steps(record, usage)
        with self._lock:
            if record.outcome_event_id is None:
                entry = self.ledger._append(
                    NaturalExecutionOutcomeEvent(
                        response_model_name_tuple=record.response_model_name_tuple,
                        natural_execution_id=record.natural_execution_id,
                        natural_block_identity=record.natural_block_identity,
                        attribution_node_id=record.node.node_id,
                        block_kind=record.block_kind,
                        status="failure",
                        failure_kind=failure_kind,
                        executor_step_count=max(record.observed_step_count, usage.request_count),
                        executor_usage=usage,
                    ),
                    parent_event_id=parent_event_id
                    if parent_event_id is not None
                    else (exhaustion_event_id if exhaustion_event_id is not None else self.anchor_event_id(record.node.node_id)),
                )
                record.outcome_event_id = entry.event_id
                record.failure_kind = failure_kind
            outcome_event_id = record.outcome_event_id
        self._unregister_natural_execution(record)
        try:
            self._report_remaining_consumption(record, usage)
        except Exception as error:  # noqa: BLE001 - Preserve the recorded failure when consumption reporting fails.
            # The failed outcome is already recorded; a failing consumption report
            # must not replace it with an untyped exception.
            emit_harness_event("consumption_report_failed", {"natural_execution_id": record.natural_execution_id, "error": type(error).__name__})
        emit_harness_event(
            "natural_execution_outcome",
            {"natural_execution_id": record.natural_execution_id, "block_kind": record.block_kind, "status": "failure", "failure_kind": failure_kind},
        )
        return NaturalExecutionError(
            natural_execution_id=record.natural_execution_id,
            outcome_event_id=outcome_event_id,
            attribution_node_id=record.node.node_id,
            failure_kind=failure_kind,
            message=message,
            raise_error_type_name=raise_error_type_name,
        )

    def settle_execution(
        self,
        record: _NaturalExecutionRecord,
        outcome: StepOutcome,
        *,
        allowed_outcome_kinds: tuple[OutcomeKind, ...],
    ) -> None:
        """Settle one Natural execution at the executor seam after the delegate returned.

        The outcome kind must be one the host accepts, every executor step is
        admitted against the allocation, and the consumption is reported. The
        write set itself is validated once by the core, admitted by
        :meth:`_inspect_step_commit`, and recorded by :meth:`_on_step_finished`.

        Raises:
            NaturalExecutionError: after recording the failed outcome event.
        """

        if record.outcome_event_id is not None:
            raise self._error_of_record(record, "the attribution node was terminated during the execution")
        if outcome.kind not in allowed_outcome_kinds:
            raise self.fail_natural_execution(record, "disallowed_outcome_kind", f"outcome kind {outcome.kind!r} is not accepted")
        usage = self._own_usage(record)
        exhaustion_event_id = self._settle_executor_steps(record, usage)
        if exhaustion_event_id is not None:
            raise self.fail_natural_execution(
                record, "allocation_exhausted", "executor steps exceeded the allocation", parent_event_id=exhaustion_event_id
            )
        try:
            self._report_remaining_consumption(record, usage)
        except Exception as error:
            raise self.fail_natural_execution(record, "commit_failed", type(error).__name__) from error
        record.executor_step_count = max(1, record.observed_step_count, usage.request_count)

    def _record_of_step_execution(self, step_execution_id: str | None, *, remove: bool) -> _NaturalExecutionRecord | None:
        if step_execution_id is None:
            return None
        with self._lock:
            if remove:
                return self._step_execution_id_to_record.pop(step_execution_id, None)
            return self._step_execution_id_to_record.get(step_execution_id)

    def _inspect_operation(self, operation: OperationRequest) -> Accept | RejectOperation:
        """Admit each operation at Core's operation boundary.

        An operation evaluated while a gateway allowance is active, such as inside a custom executor's provider request scope,
        would act with host authority, so it fails its Natural execution as a mediation bypass before evaluation.
        Otherwise parse each expression before evaluation and record detectable ``exec``, ``eval``, ``compile``, or dynamic import as a trace anomaly.
        Reject only the attempted operation and expose correction guidance while preserving the same Natural step and host accounting.
        No other expression filtering is added; evaluation remains a reversible interior effect under the architecture's graduated enforcement.

        Raises:
            NaturalExecutionError: after recording the trace anomaly and the failed outcome, for an operation under a gateway allowance.
        """

        if gateway_allowance_active():
            record = self._record_of_step_execution(operation.execution_reference.step_execution_id, remove=False)
            if record is None:
                raise CapabilityRejectedError(capability_name="natural_execution", reason_code="unknown_step_execution")
            anomaly_event_id = self.record_anomaly(
                "operation_under_gateway_allowance",
                node_id=record.node.node_id,
                detail=f"{operation.tool_name or operation.kind} operation evaluated inside a host gateway allowance",
            )
            raise self.fail_natural_execution(
                record, "mediation_bypass", "an operation was evaluated inside a host gateway allowance", parent_event_id=anomaly_event_id
            )
        expression = operation.expression or operation.argument_name_to_value.get("expression")
        if not isinstance(expression, str):
            return Accept()
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError:
            return Accept()
        dynamic_execution = detect_raw_dynamic_execution(tree)
        if dynamic_execution is None:
            return Accept()
        context = self.current_context()
        node_id = context.node.node_id if context is not None else self.run_root.node_id
        self.record_anomaly(
            "expression_raw_dynamic_execution",
            node_id=node_id,
            detail=f"tool {operation.tool_name or operation.kind} expression attempted raw dynamic execution: {dynamic_execution}",
        )
        return RejectOperation(
            f"raw dynamic execution is not allowed in Natural expressions ({dynamic_execution})",
            guidance="Use ordinary Python expressions or the available granted capabilities without exec, eval, compile, or dynamic imports.",
        )

    def _inspect_step_commit(self, commit: StepCommit) -> StepCommitDecision:
        """The host pre-commit seam: admit the validated candidate the core is about to assign.

        Installed as the core step-commit inspector of every execution scope.
        The candidate belongs to a Natural execution this host admitted; an
        execution terminated meanwhile is refused so nothing is assigned.

        Raises:
            NaturalExecutionError: when the execution was terminated before the commit.
            CapabilityRejectedError: when the candidate belongs to no admitted execution.
        """

        record = self._record_of_step_execution(commit.execution_reference.step_execution_id, remove=False)
        if record is None:
            raise CapabilityRejectedError(capability_name="natural_execution", reason_code="unknown_step_execution")
        if record.outcome_event_id is not None:
            raise self._error_of_record(record, "the attribution node was terminated before the commit")
        return Accept()

    def _on_step_finished(self, finished: StepFinished) -> None:
        """The host terminal seam: record exactly one outcome event for the finished Natural execution.

        Installed as the core step lifecycle of every execution scope and
        delivered once after the core's caller assignments. Completion records
        the committed write set; a DSL raise, a core failure, or an interruption
        records a failed outcome. Failures surface to the caller as the host's
        typed exception chained to the original; an outcome the host already
        recorded (a rejection raised at the executor or commit seam) is
        re-raised unchanged.
        """

        record = self._record_of_step_execution(finished.execution_reference.step_execution_id, remove=True)
        if record is None:
            record = self._unadmitted_record(finished)
            if record is None:
                return
        if isinstance(finished, StepCompleted):
            self._record_completion(record, finished)
            return
        if isinstance(finished, StepRaised):
            raise self.fail_natural_execution(
                record, "raise_outcome", finished.outcome.message, raise_error_type_name=finished.outcome.error_type.__name__
            ) from finished.exception
        if isinstance(finished, StepInterrupted):
            self.fail_natural_execution(record, "interrupted", f"{finished.failure_stage}: {type(finished.original_exception).__name__}")
            return
        original = finished.original_exception
        if (
            isinstance(original, NaturalExecutionError)
            and original.natural_execution_id == record.natural_execution_id
            and record.outcome_event_id is not None
        ):
            raise original
        failure_kind = _FAILURE_STAGE_TO_FAILURE_KIND.get(finished.failure_stage, "core_failure")
        raise self.fail_natural_execution(record, failure_kind, f"{finished.failure_stage}: {type(original).__name__}") from original

    def _unadmitted_record(self, finished: StepFinished) -> _NaturalExecutionRecord | None:
        """A record for a step the core finished before the host admitted it (for example a preparation failure)."""

        context = self.current_context()
        if context is None:
            return None
        return _NaturalExecutionRecord(
            natural_execution_id=generate_ulid(),
            natural_block_identity=finished.execution_reference.source_location or "<unknown>",
            block_kind=finished.block_kind,
            step_execution_id=finished.execution_reference.step_execution_id or generate_ulid(),
            node=context.node,
            meter=UsageMeter(),
        )

    def _record_completion(self, record: _NaturalExecutionRecord, finished: StepCompleted) -> None:
        if record.outcome_event_id is not None:
            raise self._error_of_record(record, "the attribution node was terminated before the commit")
        committed = dict(finished.assigned_binding_name_to_value)
        try:
            committed_snapshot = snapshot_value(committed)
            with self._lock:
                if record.outcome_event_id is None:
                    entry = self.ledger._append(
                        NaturalExecutionOutcomeEvent(
                            response_model_name_tuple=record.response_model_name_tuple,
                            natural_execution_id=record.natural_execution_id,
                            natural_block_identity=record.natural_block_identity,
                            attribution_node_id=record.node.node_id,
                            block_kind=record.block_kind,
                            status="success",
                            outcome_kind=finished.outcome.kind,
                            committed_binding_name_tuple=tuple(committed),
                            committed_binding_name_to_value=cast(MappingProxyType[str, object], committed_snapshot),
                            executor_step_count=max(1, record.executor_step_count),
                            executor_usage=record.own_usage if record.own_usage is not None else ExecutionUsage(),
                        ),
                        parent_event_id=self.anchor_event_id(record.node.node_id),
                    )
                    record.outcome_event_id = entry.event_id
        except Exception as error:
            raise self.fail_natural_execution(record, "commit_failed", type(error).__name__) from error
        self._unregister_natural_execution(record)
        emit_harness_event(
            "natural_execution_outcome",
            {
                "natural_execution_id": record.natural_execution_id,
                "block_kind": record.block_kind,
                "status": "success",
                "outcome_kind": finished.outcome.kind,
                "committed_binding_count": len(committed),
            },
        )

    def _fail_skipped_commit(self, record: _NaturalExecutionRecord) -> None:
        """Record that the core never delivered the terminal record of an admitted Natural execution."""

        if record.outcome_event_id is not None:
            return
        anomaly_event_id = self.record_anomaly(
            "commit_hook_skipped",
            node_id=record.node.node_id,
            detail=f"the core finished Natural execution {record.natural_execution_id} without delivering its terminal record to the host",
        )
        self.fail_natural_execution(record, "commit_failed", "the core terminal delivery seam was skipped", parent_event_id=anomaly_event_id)

    def fail_pending_natural_executions(self) -> None:
        """Give every Natural execution the core never finished a terminal event at run end."""

        with self._lock:
            pending_tuple = tuple(self._step_execution_id_to_record.values())
            self._step_execution_id_to_record.clear()
        for record in pending_tuple:
            self._fail_skipped_commit(record)

    @staticmethod
    def capability_of(value: object) -> Capability | None:
        """Return the capability behind a host wrapper, or ``None``."""

        capability = getattr(value, CAPABILITY_ATTRIBUTE_NAME, None)
        return capability if isinstance(capability, Capability) else None

    @staticmethod
    def host_of_wrapper(value: object) -> Host | None:
        host = getattr(value, HOST_ATTRIBUTE_NAME, None)
        return host if isinstance(host, Host) else None

    def capability_wrapper(self, capability: Capability) -> Callable[..., object]:
        with self._lock:
            wrapper = self._capability_to_wrapper.get(capability)
            if wrapper is None:
                wrapper = build_capability_wrapper(self, capability)
                self._capability_to_wrapper[capability] = wrapper
            return wrapper

    def _terminate_node_set(self, affected_node_id_tuple: tuple[str, ...], *, parent_event_id: str | None) -> None:
        with self._lock:
            self._terminated_node_id_set.update(affected_node_id_tuple)
            active_records: list[_NaturalExecutionRecord] = []
            for node_id in affected_node_id_tuple:
                active_records.extend(self._node_id_to_natural_execution_id_to_record.pop(node_id, {}).values())
        for record in active_records:
            self.fail_natural_execution(record, "terminated", "the attribution node was terminated", parent_event_id=parent_event_id)
        for node_id in affected_node_id_tuple:
            node = self.node(node_id)
            if node is not None and node.kind == "dispatch":
                self.record_dispatch_outcome(node, "host_termination", detail="terminated by the host", parent_event_id=parent_event_id)

    def _terminate_branches(self, request: TerminationRequest) -> TerminationReport:
        with host_service_scope():
            return self.runner.termination_mechanism.terminate(request)

    def terminate_descendant(self, target_node_id: str) -> TerminationCompletion:
        """Request forced termination of a descendant branch and its descendants."""

        context = self.current_context()
        if context is None:
            raise CapabilityRejectedError(capability_name="terminate_descendant", reason_code="no_active_run")
        target = self.node(target_node_id)
        if target is None:
            raise TerminationError(target_dispatch_node_id=target_node_id, reason="unknown_node")
        if not self._is_descendant(target_node_id, context.node.node_id):
            raise TerminationError(target_dispatch_node_id=target_node_id, reason="not_descendant")
        with self._lock:
            candidate_tuple = (target_node_id, *self._descendant_node_id_tuple(target_node_id))
            affected_node_id_tuple = tuple(node_id for node_id in candidate_tuple if node_id in self._nonterminal_dispatch_node_id_set)
        request_entry = self.ledger._append(
            TerminationRequestEvent(
                requesting_node_id=context.node.node_id,
                target_node_id=target_node_id,
                affected_node_id_tuple=affected_node_id_tuple,
            )
        )
        if not affected_node_id_tuple:
            completion_entry = self.ledger._append(
                TerminationCompletionEvent(
                    request_event_id=request_entry.event_id, target_node_id=target_node_id, affected_node_id_tuple=(), ceased=True
                ),
                parent_event_id=request_entry.event_id,
            )
            return TerminationCompletion(target_node_id=target_node_id, affected_node_id_tuple=(), completion_event_id=completion_entry.event_id)
        report = self._terminate_branches(
            TerminationRequest(
                run_id=self.run_id,
                requesting_node_id=context.node.node_id,
                target_node_id=target_node_id,
                affected_node_id_tuple=affected_node_id_tuple,
            )
        )
        if not report.ceased:
            self.ledger._append(
                TerminationCompletionEvent(
                    request_event_id=request_entry.event_id,
                    target_node_id=target_node_id,
                    affected_node_id_tuple=affected_node_id_tuple,
                    ceased=False,
                ),
                parent_event_id=request_entry.event_id,
            )
            raise TerminationError(target_dispatch_node_id=target_node_id, reason="cessation_not_established", detail=report.detail or None)
        self._terminate_node_set(affected_node_id_tuple, parent_event_id=request_entry.event_id)
        completion_entry = self.ledger._append(
            TerminationCompletionEvent(
                request_event_id=request_entry.event_id,
                target_node_id=target_node_id,
                affected_node_id_tuple=affected_node_id_tuple,
                ceased=True,
            ),
            parent_event_id=request_entry.event_id,
        )
        return TerminationCompletion(
            target_node_id=target_node_id,
            affected_node_id_tuple=affected_node_id_tuple,
            completion_event_id=completion_entry.event_id,
        )

    def terminate_run(self, reason: str) -> bool:
        """Host-directed run termination; returns whether interior cessation was established.

        Every active interior branch, including the run root while the scaffold
        composition is still executing, is presented to the platform termination
        mechanism. When the platform establishes cessation, the run-end host seam
        is performed here: the affected outcomes and the run-end event are
        recorded and no interior operation is admitted afterwards. When it does
        not, the run remains open without a run-end event.
        """

        with self._lock:
            if self._run_end_event_id is not None:
                return True
            self._terminated = True
        ceased = self.terminate_all_branches(detail=reason, include_run_root=True)
        if ceased:
            self.record_run_end("host_termination", detail="host-directed termination")
        return ceased

    def terminate_all_branches(self, *, detail: str, include_run_root: bool) -> bool:
        """Terminate every active interior branch through the platform mechanism.

        The affected set is every nonterminal dispatch node plus, when
        ``include_run_root`` is set and the scaffold composition is still
        executing, the run root. Returns whether every affected branch is
        established as ceased; only then are the affected Natural-execution and
        dispatch outcomes recorded. An empty affected set is trivially ceased.
        """

        with self._lock:
            affected_node_id_list = sorted(self._nonterminal_dispatch_node_id_set)
            if include_run_root and self._run_root_active:
                affected_node_id_list.insert(0, self.run_root.node_id)
        affected_node_id_tuple = tuple(affected_node_id_list)
        request_entry = self.ledger._append(
            TerminationRequestEvent(
                requesting_node_id=self.run_root.node_id,
                target_node_id=self.run_root.node_id,
                affected_node_id_tuple=affected_node_id_tuple,
            )
        )
        ceased = True
        if affected_node_id_tuple:
            report = self._terminate_branches(
                TerminationRequest(
                    run_id=self.run_id,
                    requesting_node_id=self.run_root.node_id,
                    target_node_id=self.run_root.node_id,
                    affected_node_id_tuple=affected_node_id_tuple,
                )
            )
            ceased = report.ceased
            if ceased:
                self._terminate_node_set(affected_node_id_tuple, parent_event_id=request_entry.event_id)
        self.ledger._append(
            TerminationCompletionEvent(
                request_event_id=request_entry.event_id,
                target_node_id=self.run_root.node_id,
                affected_node_id_tuple=affected_node_id_tuple,
                ceased=ceased,
            ),
            parent_event_id=request_entry.event_id,
        )
        emit_harness_event("run_termination", {"detail": detail, "ceased": ceased})
        return ceased
