import os
from dataclasses import replace

import pytest

import nighthawk as nh
from nighthawk import function_call_scope
from nighthawk.agent import NaturalExecutionOutcomeEvent, RunHandle
from nighthawk.agent.runtime import bounded_resource_allocation
from tests.nighthawk.agent.conftest import make_runner
from tests.nighthawk.agent.lazy_executor_support import _answer_async, _run
from tests.nighthawk.agent.milestone_zero_support import LazyReportingExecutor, NaturalCallBoundary


@pytest.mark.integration
@pytest.mark.skipif("OPENAI_API_KEY" not in os.environ, reason="OPENAI_API_KEY is not set")
async def test_live_lazy_single_model_reports_actual_request_usage() -> None:
    model = "openai-responses:gpt-6-luna"
    executor = LazyReportingExecutor(
        lambda: nh.AgentStepExecutor(
            configuration=nh.StepExecutorConfiguration(
                execution_model=model,
                execution_model_settings={"max_tokens": 4000},
            )
        )
    )
    assert executor.construction_count == 0
    handle = RunHandle()
    assert _run(executor, handle) == 7
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert 0 < outcome.executor_step_count <= 24
    assert outcome.executor_usage.total_token_count > 0
    print(f"model={model} observation=executor_reports requests={outcome.executor_step_count} usage={outcome.executor_usage}")
    runner = replace(
        make_runner(
            accepts_aggregate_executor_accounting=False,
            allocation_factory=lambda identity: bounded_resource_allocation(
                identity=identity.run_id,
                maximum_executor_step_count=24,
                maximum_total_token_count=60000,
            ),
        ),
        step_executor=executor,
    )
    boundary = NaturalCallBoundary(runner, {_answer_async: int})
    with function_call_scope(boundary):
        assert await _answer_async() == 7
    async_handle = boundary.handle_list[0]
    async_outcome = async_handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(async_outcome, NaturalExecutionOutcomeEvent)
    assert 0 < async_outcome.executor_usage.request_count <= 24
    assert async_handle.run_id != handle.run_id
    assert executor.construction_count == 1
    assert async_outcome.response_model_name_tuple
    print(f"async model_responses={async_outcome.response_model_name_tuple} observation=executor_reports usage={async_outcome.executor_usage}")
