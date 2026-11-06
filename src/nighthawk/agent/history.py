"""Deterministic causal analysis and bounded evidence from live ledger snapshots.

Analysis describes host observations, never durable audit completeness. Projection
omits host usage and allocation fields and is a lossy summary, not ledger storage.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._ledger import (
    DefinitionMaterializationEvent,
    DispatchEvent,
    DispatchOutcomeEvent,
    ExecutorUsage,
    LedgerEntry,
    NaturalExecutionOutcomeEvent,
    PerimeterEffectAttemptEvent,
    PerimeterEffectCompletionEvent,
    ResourceExhaustionEvent,
    RunEndEvent,
    RunLedger,
    TraceAnomalyEvent,
)

__all__ = [
    "CausalNode",
    "EvidenceReference",
    "HistoryBounds",
    "HistoryEvidence",
    "HistoryProjection",
    "HistoryQuery",
    "RunAnalysis",
    "analyze_run",
    "project_history",
    "resolve_evidence",
]


class _FrozenRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceReference(_FrozenRecord):
    run_id: str
    event_id: str
    value_path_tuple: tuple[str | int, ...] = ()


def resolve_evidence(ledger: RunLedger, reference: EvidenceReference) -> object:
    """Resolve an exact observation in a caller-selected ledger.

    Resolution establishes existence, not relevance or semantic support for a claim.
    Paths select recorded fields, mapping keys, or sequence items; they never evaluate source.
    Missing runs, events, and paths raise ValueError instead of inventing observations.
    """
    entry = ledger.find(reference.event_id) if reference.run_id == ledger.run_id else None
    if entry is None:
        raise ValueError("Evidence run or event is unavailable")
    value: object = entry.event
    for component in reference.value_path_tuple:
        if isinstance(value, BaseModel) and isinstance(component, str) and component in type(value).model_fields:
            value = getattr(value, component)
        elif dataclasses.is_dataclass(value) and not isinstance(value, type) and component in {field.name for field in dataclasses.fields(value)}:
            value = getattr(value, str(component))
        elif isinstance(value, Mapping) and component in value:
            value = value.get(component)
        elif isinstance(value, tuple | list) and isinstance(component, int) and -len(value) <= component < len(value):
            value = value[component]
        else:
            raise ValueError("Evidence value path is unavailable")
    return value


class HistoryEvidence(_FrozenRecord):
    reference: EvidenceReference
    sequence: int
    excerpt: str
    incomplete: bool


class HistoryQuery(_FrozenRecord):
    starting_sequence: int = Field(default=0, ge=0)
    event_kind_tuple: tuple[str, ...] = ()
    attribution_node_id: str | None = None


class HistoryBounds(_FrozenRecord):
    maximum_event_count: int = Field(default=20, gt=0)
    maximum_character_count: int = Field(default=6000, gt=0)
    maximum_excerpt_character_count: int = Field(default=1000, gt=0)


class HistoryProjection(_FrozenRecord):
    """A captured horizon with bounded excerpts and advancing event pagination.

    Character limits count excerpt text. Identity metadata remains explicit.
    Incomplete excerpts cannot be recovered merely by requesting the next page.
    """

    run_id: str
    horizon_sequence: int | None
    evidence_tuple: tuple[HistoryEvidence, ...]
    next_starting_sequence: int | None
    incomplete: bool


class CausalNode(_FrozenRecord):
    node_id: str
    parent_node_id: str | None
    dispatch_kind: str | None
    definition_id: str | None
    status: str
    own_usage: ExecutorUsage
    subtree_usage: ExecutorUsage
    event_id_tuple: tuple[str, ...]


class RunAnalysis(_FrozenRecord):
    run_id: str
    horizon_sequence: int | None
    evidence_basis: Literal["live_ledger_snapshot"] = "live_ledger_snapshot"
    node_tuple: tuple[CausalNode, ...]
    failure_reference_tuple: tuple[EvidenceReference, ...]
    active_node_id_tuple: tuple[str, ...]
    unmatched_effect_attempt_tuple: tuple[EvidenceReference, ...]


def _node_relations(entries: tuple[LedgerEntry, ...]) -> dict[str, str | None]:
    node_id_to_parent: dict[str, str | None] = {}
    for entry in entries:
        event = entry.event
        if isinstance(event, DispatchEvent):
            node_id_to_parent.setdefault(event.parent_node_id, None)
            node_id_to_parent[event.dispatch_node_id] = event.parent_node_id
        elif isinstance(event, DefinitionMaterializationEvent):
            node_id_to_parent.setdefault(event.materialization_origin_node_id, None)
        else:
            node_id = getattr(event, "attribution_node_id", None)
            if node_id is not None:
                node_id_to_parent.setdefault(node_id, None)
    return node_id_to_parent


def _subtree(node_id: str, node_id_to_parent: Mapping[str, str | None]) -> set[str]:
    selected = {node_id}
    while True:
        added = {child for child, parent in node_id_to_parent.items() if parent in selected} - selected
        if not added:
            return selected
        selected.update(added)


def _event_node(entry: LedgerEntry, event_id_to_entry: Mapping[str, LedgerEntry]) -> str | None:
    event = entry.event
    if isinstance(event, DispatchEvent | DispatchOutcomeEvent):
        return event.dispatch_node_id
    if isinstance(event, DefinitionMaterializationEvent):
        return event.materialization_origin_node_id
    node_id = getattr(event, "attribution_node_id", None)
    if node_id is not None:
        return node_id
    parent_id = event.attempt_event_id if isinstance(event, PerimeterEffectCompletionEvent) else entry.parent_event_id
    if parent_id is not None and parent_id in event_id_to_entry:
        return _event_node(event_id_to_entry[parent_id], event_id_to_entry)
    return None


def analyze_run(ledger: RunLedger) -> RunAnalysis:
    """Capture once; sum attributed own usage and each descendant exactly once.

    Only recorded node and event identities establish ancestry. An unmatched
    perimeter attempt is indeterminate; an admitted node without an outcome is
    active. Failed observations remain visible after later successful work.
    """
    entries = ledger.entries
    node_id_to_parent = _node_relations(entries)
    event_id_to_entry = {entry.event_id: entry for entry in entries}
    node_id_to_dispatch = {entry.event.dispatch_node_id: entry.event for entry in entries if isinstance(entry.event, DispatchEvent)}
    node_id_to_outcome = {entry.event.dispatch_node_id: entry.event for entry in entries if isinstance(entry.event, DispatchOutcomeEvent)}
    node_id_to_usage: dict[str, ExecutorUsage] = {}
    failure_list: list[EvidenceReference] = []
    completed_attempt_set: set[str] = set()
    run_end = next((entry.event for entry in entries if isinstance(entry.event, RunEndEvent)), None)
    for entry in entries:
        event = entry.event
        if isinstance(event, NaturalExecutionOutcomeEvent):
            node_id_to_usage[event.attribution_node_id] = node_id_to_usage.get(event.attribution_node_id, ExecutorUsage()) + event.executor_usage
        if isinstance(event, PerimeterEffectCompletionEvent):
            completed_attempt_set.add(event.attempt_event_id)
        if (
            getattr(event, "status", None) in {"failure", "rejected", "host_termination"}
            or isinstance(event, ResourceExhaustionEvent | TraceAnomalyEvent)
            or isinstance(event, RunEndEvent)
            and event.terminal_kind != "accepted"
        ):
            failure_list.append(EvidenceReference(run_id=ledger.run_id, event_id=entry.event_id))
    node_list: list[CausalNode] = []
    for node_id, parent in node_id_to_parent.items():
        dispatch_event = node_id_to_dispatch.get(node_id)
        outcome = node_id_to_outcome.get(node_id)
        status = outcome.status if outcome is not None else "active"
        if dispatch_event is None and parent is None and run_end is not None:
            status = run_end.terminal_kind
        usage = ExecutorUsage()
        for descendant in _subtree(node_id, node_id_to_parent):
            usage += node_id_to_usage.get(descendant, ExecutorUsage())
        node_list.append(
            CausalNode(
                node_id=node_id,
                parent_node_id=parent,
                dispatch_kind=dispatch_event.dispatch_kind if dispatch_event is not None else None,
                definition_id=dispatch_event.definition_id if dispatch_event is not None else None,
                status=status,
                own_usage=node_id_to_usage.get(node_id, ExecutorUsage()),
                subtree_usage=usage,
                event_id_tuple=tuple(entry.event_id for entry in entries if _event_node(entry, event_id_to_entry) == node_id),
            )
        )
    return RunAnalysis(
        run_id=ledger.run_id,
        horizon_sequence=max((entry.sequence for entry in entries), default=None),
        node_tuple=tuple(node_list),
        failure_reference_tuple=tuple(failure_list),
        active_node_id_tuple=tuple(node.node_id for node in node_list if node.status == "active"),
        unmatched_effect_attempt_tuple=tuple(
            EvidenceReference(run_id=ledger.run_id, event_id=entry.event_id)
            for entry in entries
            if isinstance(entry.event, PerimeterEffectAttemptEvent) and entry.event_id not in completed_attempt_set
        ),
    )


def _plain_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return {name: _plain_value(getattr(value, name)) for name in type(value).model_fields}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain_value(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        if all(type(key) is str for key in value):
            return {key: _plain_value(item) for key, item in value.items()}
        return {"entry_tuple": [[_plain_value(key), _plain_value(item)] for key, item in value.items()]}
    if isinstance(value, tuple | list):
        return [_plain_value(item) for item in value]
    if isinstance(value, frozenset | set):
        return sorted((_plain_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


_OMITTED_HOST_FIELD_SET = frozenset({"executor_usage", "executor_step_count", "resource_allocation_identity", "recursion_depth"})


def project_history(entries: tuple[LedgerEntry, ...], request: HistoryQuery, *, run_id: str, bounds: HistoryBounds) -> HistoryProjection:
    """Summarize eligible events without allocation arithmetic or implicit history.

    A page must fit one complete bounded excerpt (possibly itself shortened by
    the per-excerpt limit). Otherwise reject the unusable total bound. Pagination
    advances past the last examined event when eligible events remain.
    Mappings with non-string keys use explicit key/value pairs in their excerpts;
    rendering cannot merge distinct observations by stringifying their keys.
    """
    node_id_to_parent = _node_relations(entries)
    selected_nodes = _subtree(request.attribution_node_id, node_id_to_parent) if request.attribution_node_id is not None else None
    event_id_to_entry = {entry.event_id: entry for entry in entries}
    eligible = tuple(
        entry
        for entry in sorted(entries, key=lambda entry: entry.sequence)
        if entry.sequence >= request.starting_sequence
        and (not request.event_kind_tuple or entry.event.kind in request.event_kind_tuple)
        and (selected_nodes is None or _event_node(entry, event_id_to_entry) in selected_nodes)
    )
    evidence_list: list[HistoryEvidence] = []
    character_count = 0
    for entry in eligible:
        field_name_to_value = {
            name: _plain_value(getattr(entry.event, name)) for name in type(entry.event).model_fields if name not in _OMITTED_HOST_FIELD_SET
        }
        text = json.dumps(field_name_to_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        excerpt = text[: bounds.maximum_excerpt_character_count]
        if character_count + len(excerpt) > bounds.maximum_character_count or len(evidence_list) >= bounds.maximum_event_count:
            if not evidence_list:
                raise ValueError("maximum_character_count cannot fit one bounded excerpt")
            break
        evidence_list.append(
            HistoryEvidence(
                reference=EvidenceReference(run_id=run_id, event_id=entry.event_id),
                sequence=entry.sequence,
                excerpt=excerpt,
                incomplete=len(excerpt) < len(text) or bool(_OMITTED_HOST_FIELD_SET.intersection(type(entry.event).model_fields)),
            )
        )
        character_count += len(excerpt)
    remaining = len(evidence_list) < len(eligible)
    return HistoryProjection(
        run_id=run_id,
        horizon_sequence=max((entry.sequence for entry in entries), default=None),
        evidence_tuple=tuple(evidence_list),
        next_starting_sequence=evidence_list[-1].sequence + 1 if remaining else None,
        incomplete=remaining or any(evidence.incomplete for evidence in evidence_list),
    )
