"""The run ledger is append-only with stable identities and one total order."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType

import pytest

from nighthawk.agent import (
    DispatchEvent,
    DispatchOutcomeEvent,
    EffectFootprint,
    ExecutorUsage,
    NaturalExecutionOutcomeEvent,
    RunLedger,
    TraceAnomalyEvent,
)
from nighthawk.agent._ledger import snapshot_value


def _dispatch_event(node_id: str) -> DispatchEvent:
    return DispatchEvent(
        dispatch_node_id=node_id,
        parent_node_id="root",
        recursion_depth=1,
        dispatch_kind="definition_only",
        defining_source_identity="abc",
        definition_id=None,
        export_name_tuple=("f",),
        dependency_name_tuple=(),
        effect_footprint=EffectFootprint(),
        grant_capability_name_tuple=(),
        confinement_identity="test",
    )


def test_append_assigns_sequence_identity_and_parent() -> None:
    ledger = RunLedger("run")
    first = ledger._append(_dispatch_event("node"))
    second = ledger._append(DispatchOutcomeEvent(dispatch_node_id="node", status="success"), parent_event_id=first.event_id)
    assert [entry.sequence for entry in ledger.entries] == [0, 1]
    assert first.event_id != second.event_id
    assert second.parent_event_id == first.event_id
    assert ledger.find(second.event_id) is second
    assert len(ledger) == 2


def test_entries_snapshot_is_immutable_and_public_surface_has_no_append() -> None:
    ledger = RunLedger("run")
    ledger._append(_dispatch_event("node"))
    snapshot = ledger.entries
    assert isinstance(snapshot, tuple)
    assert not hasattr(RunLedger, "append")
    assert not hasattr(RunLedger, "remove")


def test_dispatch_status_derives_only_from_dispatch_and_outcome_events() -> None:
    ledger = RunLedger("run")
    assert ledger.dispatch_status("node") is None
    ledger._append(_dispatch_event("node"))
    assert ledger.dispatch_status("node") == "nonterminal"
    ledger._append(TraceAnomalyEvent(anomaly_kind="definition_code_modified", attribution_node_id="node", detail="x"))
    assert ledger.dispatch_status("node") == "nonterminal"
    ledger._append(DispatchOutcomeEvent(dispatch_node_id="node", status="host_termination"))
    assert ledger.dispatch_status("node") == "host_termination"


def test_select_filters_by_event_type_in_commit_order() -> None:
    ledger = RunLedger("run")
    ledger._append(_dispatch_event("a"))
    ledger._append(DispatchOutcomeEvent(dispatch_node_id="a", status="failure", failure_class_name="ValueError"))
    ledger._append(_dispatch_event("b"))
    selected = ledger.select(DispatchEvent)
    assert [entry.event.dispatch_node_id for entry in selected] == ["a", "b"]  # type: ignore[union-attr]


def _outcome(node_id: str, steps: int, tokens: int) -> NaturalExecutionOutcomeEvent:
    return NaturalExecutionOutcomeEvent(
        natural_execution_id=f"execution-{node_id}-{steps}",
        natural_block_identity="module:1",
        attribution_node_id=node_id,
        block_kind="execute",
        status="success",
        outcome_kind="pass",
        executor_step_count=steps,
        executor_usage=ExecutorUsage(request_count=steps, total_token_count=tokens),
    )


def test_attribution_aggregates_deterministically_over_the_tree() -> None:
    ledger = RunLedger("run")
    ledger._append(_outcome("root", 1, 10))
    ledger._append(_dispatch_event("child"))
    ledger._append(_outcome("child", 2, 5))
    ledger._append(_outcome("root", 3, 1))
    assert ledger.dispatch_count() == 1
    assert ledger.executor_step_count() == 6
    assert ledger.executor_step_count("child") == 2
    assert ledger.attribution_node_id_to_executor_usage() == {
        "root": ExecutorUsage(request_count=4, total_token_count=11),
        "child": ExecutorUsage(request_count=2, total_token_count=5),
    }


def test_snapshot_value_freezes_containers_and_replaces_live_callables() -> None:
    @dataclass(frozen=True)
    class Frozen:
        name: str

    @dataclass
    class Mutable:
        items: list[int]

    source = {"items": [1, {2, 3}], "frozen": Frozen("a"), "mutable": Mutable([1]), "function": len}
    snapshot = snapshot_value(source)
    assert isinstance(snapshot, MappingProxyType)
    assert snapshot["items"] == (1, frozenset({2, 3}))
    assert snapshot["frozen"] == Frozen("a")
    assert snapshot["mutable"] == MappingProxyType({"items": (1,)})
    assert snapshot["function"] == "<callable builtins.len>"
    source["items"].append(4)  # type: ignore[union-attr]
    assert snapshot["items"] == (1, frozenset({2, 3}))
    cyclic: list[object] = []
    cyclic.append(cyclic)
    assert snapshot_value(cyclic) == ("<cycle>",)


def test_snapshot_value_is_total_and_looks_through_record_extras() -> None:
    from pydantic import BaseModel, ConfigDict

    class Extra(BaseModel):
        model_config = ConfigDict(frozen=True, extra="allow")

        name: str

    with_extra = Extra(name="a", notes=[])  # type: ignore[call-arg]
    snapshot = snapshot_value(with_extra)
    assert snapshot is not with_extra
    assert snapshot["notes"] == ()  # type: ignore[index]
    plain = Extra(name="a")
    assert snapshot_value(plain) is plain

    class Raising:
        def __nighthawk_ledger_snapshot__(self) -> object:
            raise ValueError("boom")

    class RaisingMapping(dict[str, object]):
        def items(self):  # type: ignore[override]
            raise RuntimeError("no items")

    assert str(snapshot_value(Raising())).startswith("<snapshot failed ")
    assert str(snapshot_value(RaisingMapping())).startswith("<snapshot failed ")
    assert snapshot_value([Raising(), 1])[1] == 1  # type: ignore[index]


@pytest.mark.parametrize("base_type, scalar", [(int, 7), (float, 1.5), (str, "recorded"), (bytes, b"recorded")])
def test_snapshot_normalizes_mutable_scalar_subclasses(base_type: type, scalar: object) -> None:
    class MutableScalar(base_type):
        pass

    value = MutableScalar(scalar)
    value.notes = []
    snapshot = snapshot_value(value)
    value.notes.append("later")
    assert type(snapshot) is base_type
    assert snapshot == scalar
    assert snapshot is not value


def test_snapshot_hook_cycles_are_detected_before_repeated_invocation() -> None:
    class Hook:
        def __init__(self) -> None:
            self.calls = 0
            self.target: object = self

        def __nighthawk_ledger_snapshot__(self) -> object:
            self.calls += 1
            return self.target

    direct = Hook()
    assert snapshot_value(direct) == "<cycle>"
    assert direct.calls == 1
    first, second = Hook(), Hook()
    first.target = {"child": second}
    second.target = first
    assert snapshot_value(first) == {"child": "<cycle>"}
    assert first.calls == second.calls == 1
    repeated = Hook()
    repeated.target = "observed"
    assert snapshot_value([repeated, repeated]) == ("observed", "observed")


def test_scalar_subclass_can_supply_an_explicit_snapshot() -> None:
    class AnnotatedInteger(int):
        def __nighthawk_ledger_snapshot__(self) -> object:
            return {"value": int(self), "notes": ["explicit"]}

    assert snapshot_value(AnnotatedInteger(7)) == {"value": 7, "notes": ("explicit",)}


def test_snapshot_includes_inherited_slots_and_undeclared_model_attributes() -> None:
    from pydantic import BaseModel, ConfigDict

    class Slotted:
        __slots__ = ("notes",)

    @dataclass(frozen=True)
    class Record(Slotted):
        value: int

    record = Record(7)
    Slotted.notes.__set__(record, [])
    snapshot = snapshot_value(record)
    assert isinstance(snapshot, Mapping)
    record.notes.append("later")
    assert snapshot == {"value": 7, "notes": ()}

    class Model(BaseModel):
        model_config = ConfigDict(frozen=True)
        value: int

        @cached_property
        def notes(self) -> list[str]:
            return []

    model = Model(value=7)
    assert model.notes == []
    model_snapshot = snapshot_value(model)
    model.notes.append("later")
    assert model_snapshot == {"value": 7, "notes": ()}


def test_snapshot_preserves_entries_when_key_projections_collide_or_are_unhashable() -> None:
    class Key:
        pass

    first, second = Key(), Key()
    snapshot = snapshot_value({first: ["first"], second: ["second"]})
    assert not isinstance(snapshot, Mapping)
    assert snapshot.entry_tuple == (  # type: ignore[attr-defined]
        (snapshot_value(first), ("first",)),
        (snapshot_value(second), ("second",)),
    )

    @dataclass(frozen=True)
    class StructuredKey:
        values: tuple[int, ...]

    structured = snapshot_value({StructuredKey((1, 2)): "observed"})
    assert structured.entry_tuple == (({"values": (1, 2)}, "observed"),)  # type: ignore[attr-defined]
    assert snapshot_value({1: "integer", "1": "text"}) == {1: "integer", "1": "text"}
    assert snapshot_value({first, second}) == (snapshot_value(first), snapshot_value(second))
