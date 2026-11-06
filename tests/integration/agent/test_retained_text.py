import os

import pytest

import nighthawk as nh
from examples.retained_text_workflow import generate_normalizer, run_normalization_trial
from nighthawk.agent import NaturalExecutionOutcomeEvent, PerimeterPolicy, RunHandle
from nighthawk.agent.runtime import Runner, bounded_resource_allocation


@pytest.mark.integration
@pytest.mark.skipif("OPENAI_API_KEY" not in os.environ, reason="Live source generation requires OPENAI_API_KEY")
async def test_live_generate_once_and_reuse_in_fresh_batch_runs() -> None:
    runner = Runner(
        step_executor=nh.AgentStepExecutor(
            nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna", execution_model_settings={"max_tokens": 4000})
        ),
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(maximum_executor_step_count=16, maximum_total_token_count=40000),
    )
    generation = RunHandle()
    function = await generate_normalizer(runner=runner, handle=generation)
    assert len(generation.ledger.select(NaturalExecutionOutcomeEvent)) == 1
    for labels in ((" Alpha ", "alpha", "BETA", ""), (" X ", "x", "Y", " ")):
        handle = RunHandle()
        trial = run_normalization_trial(function, labels, runner=runner, handle=handle)
        assert trial.matches_criterion
        assert trial.run_id != generation.run_id
        assert not handle.ledger.select(NaturalExecutionOutcomeEvent)
