from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

import nighthawk as nh
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.core.usage import ExecutionUsage
from nighthawk.harness.context import current_executor_step_admission
from nighthawk.harness.ledger import NaturalExecutionOutcomeEvent
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.runner import Runner
from nighthawk.parts.conversation import ConversationResult, UserFacingBlock
from nighthawk.session import CellInput, SessionManager


@nh.function
async def _application_call() -> int:
    result: int = 0
    """execute
    Set <:result> to 42.
    """
    return result


@pytest.mark.parametrize("exhaust", [False, True])
async def test_shared_runner_standard_conversations_keep_independent_usage_bindings_and_cancellation(exhaust: bool) -> None:
    task_to_entered = {task: asyncio.Event() for task in ("first", "second")}
    task_to_release = {task: asyncio.Event() for task in ("first", "second")}
    allocation_list: list[object] = []

    class Executor:
        admits_executor_steps = True

        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            task = cast(Any, step_context.step_locals["conversation_input"]).task.split()[0]
            admission = current_executor_step_admission()
            admission.admit()
            task_to_entered[task].set()
            await task_to_release[task].wait()
            admission.observe(ExecutionUsage(request_count=1, input_token_count=17, output_token_count=3))
            if exhaust and task == "first":
                admission.admit()
                raise AssertionError("exhausted run must refuse the next request")
            result = ConversationResult((UserFacingBlock("answer", task),), {"answer": 7})
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": result})

    def allocate(identity: Any) -> Any:
        allocation = bounded_resource_allocation(identity=identity.run_id, maximum_executor_step_count=1)
        allocation_list.append(allocation)
        return allocation

    manager = SessionManager(runner=Runner(Executor(), PerimeterPolicy.NONE, allocate))
    first, second = manager.create_session(), manager.create_session()
    del manager
    first_execution, second_execution = first.submit(CellInput("first <:answer>")), second.submit(CellInput("second <:answer>"))
    first_task, second_task = asyncio.create_task(first_execution.wait_async()), asyncio.create_task(second_execution.wait_async())
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in task_to_entered.values())), timeout=5)
        assert first_execution.outcome is second_execution.outcome is None
        assert len(first_execution.run_id_tuple) == len(second_execution.run_id_tuple) == 1
        assert first_execution.run_id_tuple != second_execution.run_id_tuple
        assert len(allocation_list) == 2 and allocation_list[0] is not allocation_list[1]
        assert first.read_run_evidence(first_execution.run_id_tuple[0]).evidence_tuple
        assert second.read_run_evidence(second_execution.run_id_tuple[0]).evidence_tuple
        with nh.run(ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": 42})])):
            assert await _application_call() == 42
        assert len(allocation_list) == 2
        if exhaust:
            task_to_release["first"].set()
        else:
            first_execution.request_interrupt()
        assert (await first_task).status == ("failure" if exhaust else "cancelled")
        assert not second_task.done()
        task_to_release["second"].set()
        assert (await second_task).status == "success"
        assert "answer" not in first.binding_name_to_value
        assert second.binding_name_to_value["answer"] == 7
        for session, execution, expected in ((first, first_execution, int(exhaust)), (second, second_execution, 1)):
            event = session._run_id_to_ledger[execution.run_id_tuple[0]].select(NaturalExecutionOutcomeEvent)[0].event
            assert isinstance(event, NaturalExecutionOutcomeEvent)
            assert event.executor_usage.request_count == expected
        assert first.close()
        assert (await second.submit(CellInput("> answer + 1")).wait_async()).status == "success"
    finally:
        for task in (first_task, second_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        first.close()
        second.close()
