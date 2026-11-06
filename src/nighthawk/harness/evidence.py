"""Deterministic causal analysis and bounded evidence from live ledger snapshots.

Analysis describes host observations, never durable audit completeness. Projection
omits host usage and allocation fields and is a lossy summary, not ledger storage.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from nighthawk.core.usage import ExecutionUsage
from nighthawk.harness.ledger import (
    DefinitionMaterializationEvent,
    DispatchEvent,
    DispatchOutcomeEvent,
    LedgerEntry,
    NaturalExecutionOutcomeEvent,
    PerimeterEffectAttemptEvent,
    PerimeterEffectCompletionEvent,
    ResourceExhaustionEvent,
    RunEndEvent,
    RunLedger,
    TraceAnomalyEvent,
)

__all__ = ["CausalNode", "EvidenceReference", "RunAnalysis", "analyze_run", "resolve_evidence", "select_subtree_entries"]


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


class CausalNode(_FrozenRecord):
    node_id: str
    parent_node_id: str | None
    dispatch_kind: str | None
    definition_id: str | None
    status: str
    own_usage: ExecutionUsage
    subtree_usage: ExecutionUsage
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


def select_subtree_entries(entries: tuple[LedgerEntry, ...], node_id: str) -> tuple[LedgerEntry, ...]:
    """Select recorded events attributed to a node and its descendants, preserving input order."""
    selected = _subtree(node_id, _node_relations(entries))
    event_id_to_entry = {entry.event_id: entry for entry in entries}
    return tuple(entry for entry in entries if _event_node(entry, event_id_to_entry) in selected)


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
    node_id_to_usage: dict[str, ExecutionUsage] = {}
    failure_list: list[EvidenceReference] = []
    completed_attempt_set: set[str] = set()
    run_end = next((entry.event for entry in entries if isinstance(entry.event, RunEndEvent)), None)
    for entry in entries:
        event = entry.event
        if isinstance(event, NaturalExecutionOutcomeEvent):
            node_id_to_usage[event.attribution_node_id] = node_id_to_usage.get(event.attribution_node_id, ExecutionUsage()) + event.executor_usage
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
        usage = ExecutionUsage()
        for descendant in _subtree(node_id, node_id_to_parent):
            usage += node_id_to_usage.get(descendant, ExecutionUsage())
        node_list.append(
            CausalNode(
                node_id=node_id,
                parent_node_id=parent,
                dispatch_kind=dispatch_event.dispatch_kind if dispatch_event is not None else None,
                definition_id=dispatch_event.definition_id if dispatch_event is not None else None,
                status=status,
                own_usage=node_id_to_usage.get(node_id, ExecutionUsage()),
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
