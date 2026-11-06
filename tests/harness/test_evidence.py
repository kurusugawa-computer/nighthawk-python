from nighthawk.harness.evidence import analyze_run
from nighthawk.harness.ledger import (
    DispatchOutcomeEvent,
    PerimeterEffectAttemptEvent,
    PerimeterEffectCompletionEvent,
    RunLedger,
)
from tests.support.ledger import _dispatch_event, _outcome


def test_nested_usage_recovered_failure_and_active_attempt() -> None:
    ledger = RunLedger("run")
    ledger._append(_dispatch_event("parent"))
    ledger._append(_dispatch_event("child").model_copy(update={"parent_node_id": "parent", "recursion_depth": 2}))
    ledger._append(_outcome("root", 1, 10))
    failure = ledger._append(_outcome("child", 2, 20).model_copy(update={"status": "failure", "failure_kind": "executor_failed"}))
    ledger._append(_outcome("child", 3, 30))
    ledger._append(_outcome("parent", 4, 40))
    attempt = ledger._append(
        PerimeterEffectAttemptEvent(capability_name="save", attribution_node_id="child", perimeter_effect_class_tuple=("durable_write_back",))
    )
    ledger._append(DispatchOutcomeEvent(dispatch_node_id="child", status="success"))
    analysis = analyze_run(ledger)
    node_id_to_node = {node.node_id: node for node in analysis.node_tuple}
    assert node_id_to_node["root"].subtree_usage.total_token_count == 100
    assert node_id_to_node["parent"].own_usage.total_token_count == 40
    assert node_id_to_node["parent"].subtree_usage.total_token_count == 90
    assert node_id_to_node["child"].own_usage.total_token_count == 50
    assert analysis.failure_reference_tuple[0].event_id == failure.event_id
    assert analysis.unmatched_effect_attempt_tuple[0].event_id == attempt.event_id
    assert set(analysis.active_node_id_tuple) == {"root", "parent"}
    assert analysis.horizon_sequence == 7
    ledger._append(PerimeterEffectCompletionEvent(attempt_event_id=attempt.event_id, capability_name="save", status="success"))
    assert analyze_run(ledger).unmatched_effect_attempt_tuple == ()
    assert analysis.unmatched_effect_attempt_tuple and analysis.horizon_sequence == 7
    assert analyze_run(ledger).evidence_basis == "live_ledger_snapshot"
