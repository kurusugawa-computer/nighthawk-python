import json

import pytest

from nighthawk.harness.context import current_capabilities, current_run_ledger
from nighthawk.harness.dispatch import dispatch
from nighthawk.harness.evidence import EvidenceReference, analyze_run, resolve_evidence
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import (
    CapabilityInvocationEvent,
    DispatchOutcomeEvent,
    RunLedger,
)
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.run_handle import RunHandle
from nighthawk.parts.evidence import EvidenceBounds, EvidenceQuery, project_evidence
from tests.support.harness import make_runner
from tests.support.ledger import _dispatch_event, _outcome
from tests.support.runs import EchoInput, EchoResult, run_body


def test_projection_pages_filter_recorded_subtrees_and_exclude_usage() -> None:
    ledger = RunLedger("run")
    ledger._append(_dispatch_event("selected"))
    ledger._append(_dispatch_event("other"))
    ledger._append(_dispatch_event("child").model_copy(update={"parent_node_id": "selected"}))
    selected = ledger._append(_outcome("selected", 1, 12345))
    ledger._append(_outcome("other", 1, 99999))
    child = ledger._append(_outcome("child", 1, 88888))
    snapshot = ledger.entries
    bounds = EvidenceBounds(maximum_event_count=1)
    query = EvidenceQuery(event_kind_tuple=("natural_execution_outcome",), attribution_node_id="selected")
    first = project_evidence(snapshot, query, run_id=ledger.run_id, bounds=bounds)
    assert first.evidence_tuple[0].reference.event_id == selected.event_id
    assert first.next_starting_sequence == selected.sequence + 1 and first.incomplete
    excerpt = first.evidence_tuple[0].excerpt
    assert "executor_usage" not in excerpt and "executor_step_count" not in excerpt and "12345" not in excerpt
    second = project_evidence(
        snapshot, query.model_copy(update={"starting_sequence": first.next_starting_sequence}), run_id=ledger.run_id, bounds=bounds
    )
    assert second.evidence_tuple[0].reference.event_id == child.event_id
    assert second.next_starting_sequence is None
    ledger._append(_outcome("selected", 2, 10))
    assert second.horizon_sequence == 5
    assert len(project_evidence(snapshot, EvidenceQuery(attribution_node_id="missing"), run_id="run", bounds=bounds).evidence_tuple) == 0


def test_excerpt_omission_total_bound_and_empty_horizon() -> None:
    ledger = RunLedger("run")
    ledger._append(DispatchOutcomeEvent(dispatch_node_id="node", status="success", result="x" * 2000))
    bounds = EvidenceBounds(maximum_excerpt_character_count=40, maximum_character_count=40)
    page = project_evidence(ledger.entries, EvidenceQuery(), run_id=ledger.run_id, bounds=bounds)
    assert len(page.evidence_tuple[0].excerpt) == 40
    assert page.incomplete and page.next_starting_sequence is None
    with pytest.raises(ValueError, match="fit"):
        project_evidence(ledger.entries, EvidenceQuery(), run_id="run", bounds=EvidenceBounds(maximum_character_count=1))
    empty = project_evidence((), EvidenceQuery(), run_id="empty-run", bounds=bounds)
    assert empty.run_id == "empty-run" and empty.horizon_sequence is None
    assert empty.evidence_tuple == () and not empty.incomplete
    assert analyze_run(RunLedger("empty")).node_tuple == ()


def test_host_snapshots_survive_live_alias_mutation_and_record_recovery() -> None:
    shared = [1]
    retained_pages = []

    def body(run_input: EchoInput) -> EchoResult:
        function = dispatch(CodeProposal(source="def fail() -> int:\n    return 1 // 0\n", export_name_tuple=("fail",))).export_name_to_value["fail"]
        with pytest.raises(ZeroDivisionError):
            function()  # type: ignore[operator]
        success = dispatch(
            CodeProposal(
                source="def recover() -> object:\n    return shared\n",
                export_name_tuple=("recover",),
                dependency_tuple=(DependencyDeclaration(name="shared", kind="reference", declared_type=list),),
            )
        ).export_name_to_value["recover"]
        assert success() == shared  # type: ignore[operator]
        ledger = current_run_ledger()
        retained_pages.append(
            project_evidence(ledger.entries, EvidenceQuery(event_kind_tuple=("dispatch_outcome",)), run_id=ledger.run_id, bounds=EvidenceBounds())
        )
        shared.append(2)
        return EchoResult()

    handle = RunHandle()
    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(shared=shared), handle=handle)
    page = retained_pages[0]
    results = [json.loads(evidence.excerpt) for evidence in page.evidence_tuple]
    assert results[-1]["result"] == [1] and shared == [1, 2]
    analysis = analyze_run(handle.ledger)
    assert any(reference.event_id for reference in analysis.failure_reference_tuple)
    assert not analysis.active_node_id_tuple
    assert [node.dispatch_kind for node in analysis.node_tuple].count("invocation") == 2


def test_capability_evidence_preserves_colliding_keys_through_commit_projection_and_lookup() -> None:
    class Key:
        pass

    first, second = Key(), Key()
    value = {first: ["first"], second: ["second"]}

    def observe() -> object:
        return value

    def body(run_input: EchoInput) -> EchoResult:
        assert current_capabilities()["observe"]() is value
        value[first].append("later")
        return EchoResult()

    handle = RunHandle()
    run_body(
        body,
        runner=make_runner(),
        run_entry_grant=Grant.of(Capability(name="observe", function=observe, authority_boundary="Observe explicitly retained data")),
        handle=handle,
    )
    entry = handle.ledger.select(CapabilityInvocationEvent)[0]
    projection = project_evidence(
        handle.ledger.entries, EvidenceQuery(event_kind_tuple=("capability_invocation",)), run_id=handle.ledger.run_id, bounds=EvidenceBounds()
    )
    evidence = projection.evidence_tuple[0]
    assert not evidence.incomplete
    pairs = json.loads(evidence.excerpt)["result"]["entry_tuple"]
    assert [item[1] for item in pairs] == [["first"], ["second"]]
    assert resolve_evidence(
        handle.ledger,
        EvidenceReference(run_id=handle.ledger.run_id, event_id=entry.event_id, value_path_tuple=("result", "entry_tuple", 0, 1)),
    ) == ("first",)


def test_projection_does_not_merge_numeric_and_string_mapping_keys() -> None:
    ledger = RunLedger("run")
    ledger._append(DispatchOutcomeEvent(dispatch_node_id="node", status="success", result={1: "integer", "1": "text"}))
    projection = project_evidence(ledger.entries, EvidenceQuery(), run_id=ledger.run_id, bounds=EvidenceBounds())
    assert json.loads(projection.evidence_tuple[0].excerpt)["result"] == {"entry_tuple": [[1, "integer"], ["1", "text"]]}
