import asyncio

import pytest

from evals.model_tools.cases import CASES, check_outcome, make_workspace
from evals.model_tools.prototype import ApprovalRejectedError, Workspace
from evals.model_tools.run import close_unawaited_values, trace_snapshot


@pytest.mark.asyncio
async def test_inspected_reference_composes_with_python_local_and_preserves_identity():
    case = next(case for case in CASES if case.name == "helper_collision")
    workspace = make_workspace(case, "refined")
    response = await workspace.operation("inspect", name="search_documents", environment="task")
    reference = response["entries"][0]["reference"]
    assert reference.startswith("nh_environment_2[")
    assert await workspace.evaluate(reference) is workspace.task_name_to_value["search_documents"]
    await workspace.operation("assign", target_path="result", expression=f"{reference}(question)")
    assert not check_outcome(case, workspace, "pass", None, workspace.output_values())


@pytest.mark.asyncio
async def test_cursor_progresses_over_stable_snapshot_and_retries_same_page():
    workspace = Workspace(task_name_to_value={f"name_{i}": i for i in range(11)})
    workspace.initialize()
    first = workspace.inspect_state(None, "task")
    workspace.task_name_to_value["new_name"] = 12
    cursor = first["next_cursor"]
    second = workspace.inspect_state(None, "effective", cursor)
    repeated = workspace.inspect_state(None, "effective", cursor)
    assert second["entries"] == repeated["entries"]
    third = workspace.inspect_state(None, "effective", second["next_cursor"])
    assert third["next_cursor"] is None
    names = [entry["name"] for page in (first, second, third) for entry in page["entries"]]
    assert len(names) == len(set(names)) == 11
    assert "new_name" not in names


@pytest.mark.asyncio
async def test_unbound_masks_parent_and_lambda_uses_same_environment():
    workspace = Workspace(python_name_to_value={"value": 7}, parent_name_to_value={"i": 5}, unbound_names={"i"})
    workspace.initialize()
    assert await workspace.evaluate("(lambda: value)()") == 7
    with pytest.raises(UnboundLocalError):
        await workspace.evaluate("i")


@pytest.mark.asyncio
async def test_preflight_prevents_effect_but_validation_does_not_undo_effect():
    effects = []

    def effect():
        effects.append("effect")
        return "invalid"

    workspace = Workspace(python_name_to_value={"readonly": 1, "effect": effect}, output_name_to_type={"result": int})
    workspace.initialize()
    rejected = await workspace.operation("assign", target_path="readonly", expression="effect()")
    assert rejected["evaluation_started"] is False
    assert not effects
    invalid = await workspace.operation("assign", target_path="result", expression="effect()")
    assert invalid["evaluation_started"] is True
    assert invalid["error_type"] == "ValidationError"
    assert effects == ["effect"]
    assert "result" not in workspace.step_name_to_value


@pytest.mark.asyncio
async def test_nested_fixture_reads_materialized_counter_for_all_ten_calls():
    case = next(case for case in CASES if case.name == "nested")
    workspace = make_workspace(case, "refined")
    await workspace.operation("assign", target_path="result", expression="[]")
    for i in range(1, 11):
        await workspace.operation("assign", target_path="i", expression=str(i))
        await workspace.operation("evaluate", expression="result.append(await block_two())")
    assert not check_outcome(case, workspace, "pass", None, workspace.output_values())


@pytest.mark.asyncio
async def test_cancellation_during_approval_does_not_start_effect():
    entered = asyncio.Event()
    approved = asyncio.Event()
    effects = []

    async def action():
        entered.set()
        await approved.wait()
        effects.append("executed")

    workspace = Workspace(python_name_to_value={"action": action})
    workspace.initialize()
    task = asyncio.create_task(workspace.operation("evaluate", expression="action()"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert effects == []


@pytest.mark.asyncio
async def test_rejection_is_terminal_and_has_no_effect():
    case = next(case for case in CASES if case.name == "rejection")
    workspace = make_workspace(case, "refined")
    with pytest.raises(ApprovalRejectedError):
        await workspace.operation("evaluate", expression="rejected_action()")
    assert not check_outcome(case, workspace, "rejected", None, {})


def test_trace_can_record_unexpected_coroutine_without_running_it():
    effects = []

    async def pending():
        effects.append("executed")

    value = pending()
    snapshot = trace_snapshot({"returned": [value]})
    close_unawaited_values([value])
    assert snapshot == {"returned": [{"runtime_type": "coroutine"}]}
    assert effects == []


@pytest.mark.asyncio
async def test_syntax_failure_reports_that_evaluation_did_not_start():
    workspace = Workspace(output_name_to_type={"result": int})
    workspace.initialize()
    response = await workspace.operation("evaluate", expression="result = 1")
    assert response["error_type"] == "SyntaxError"
    assert response["evaluation_started"] is False
    assert "single Python expression" in response["guidance"]


def test_trace_handles_cyclic_model_values():
    value = []
    value.append(value)
    assert trace_snapshot(value) == [{"runtime_type": "list", "circular": True}]
