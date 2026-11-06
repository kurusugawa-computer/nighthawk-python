"""Live provider smoke for the canonical reference loop and host-mediated decisions.

Gated behind ``pytest.mark.integration`` and skipped without credentials;
select ``pytest -m "not integration"`` to avoid live provider calls.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

import nighthawk as nh
from nighthawk import AgentStepExecutor, StepExecutorConfiguration
from nighthawk.agent import Grant, NaturalExecutionOutcomeEvent, RunHandle, execute_code_capability
from nighthawk.agent.reference import ReferenceLoopResult, run_reference_loop
from tests.nighthawk.agent.conftest import make_runner
from tests.nighthawk.agent.support import EchoInput, EchoResult, run_body

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "OPENAI_API_KEY" not in os.environ,
        reason="OPENAI_API_KEY is not set; skipping live LLM integration tests",
    ),
]


_INTEGRATION_MODEL = "openai-responses:gpt-6-luna"
_INTEGRATION_DECISION_MODEL = "openai-decisions:gpt-6-luna"


@nh.function
def _states_arithmetic_fact(text: str) -> bool:
    states_fact: bool = False
    """decide <:states_fact>
    Is <text> a correct arithmetic statement?
    """
    return states_fact


def test_reference_loop_completes_with_a_typed_result() -> None:
    executor = AgentStepExecutor(configuration=StepExecutorConfiguration(execution_model=_INTEGRATION_MODEL))
    result = run_reference_loop(
        "Define a Python function that returns the square of an integer, call it for 12, and summarize the result.",
        run_entry_grant=Grant.of(execute_code_capability()),
        runner=replace(make_runner(), step_executor=executor),
    )
    assert isinstance(result, ReferenceLoopResult)
    assert result.summary


def test_decision_is_admitted_accounted_and_recorded_with_its_block_kind() -> None:
    executor = AgentStepExecutor(configuration=StepExecutorConfiguration(decision_model=_INTEGRATION_DECISION_MODEL))
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=f"{_states_arithmetic_fact('2 + 2 = 4')}:{_states_arithmetic_fact('2 + 2 = 5')}")

    result = run_body(body, runner=make_runner(accepts_aggregate_executor_accounting=False), step_executor=executor, handle=handle)
    assert result.value == "True:False"
    outcome_list = [
        entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent) if isinstance(entry.event, NaturalExecutionOutcomeEvent)
    ]
    assert [(event.block_kind, event.status, event.outcome_kind) for event in outcome_list] == [("decide", "success", "pass")] * 2
    for event in outcome_list:
        assert event.executor_step_count == event.executor_usage.request_count == 1
        assert event.executor_usage.total_token_count > 0
        assert all(name is not None and "luna" in name for name in event.response_model_name_tuple)
        print(f"decision={dict(event.committed_binding_name_to_value)} models={event.response_model_name_tuple} usage={event.executor_usage}")
