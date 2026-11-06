from __future__ import annotations

import pytest

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness._natural_execution import executor_step_observation_of
from nighthawk.harness.errors import NaturalExecutionError, RunFailedError
from nighthawk.harness.ledger import NaturalExecutionOutcomeEvent, ResourceExhaustionEvent, RunEntryEvent
from nighthawk.harness.resources import ResourceConsumption, ResourceRejection, ResourceRequest
from nighthawk.harness.run_handle import RunHandle
from tests.support.harness import make_runner
from tests.support.runs import DecisionsTransport, EchoInput, EchoResult, decision_model, run_body


@nh.function
def _supported(text: str) -> bool:
    supported: bool = False
    """decide <:supported>
    Does <text> state a supported claim?
    """
    return supported


def _outcome_list(handle: RunHandle) -> list[NaturalExecutionOutcomeEvent]:
    return [entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent) if isinstance(entry.event, NaturalExecutionOutcomeEvent)]


def _decision_executor(transport: DecisionsTransport) -> AgentStepExecutor:
    return AgentStepExecutor(configuration=StepExecutorConfiguration(decision_model=decision_model(transport)))


def _judge(run_input: EchoInput) -> EchoResult:
    return EchoResult(value=str(_supported(run_input.text)))


def test_decision_requests_of_the_core_agent_executor_are_admitted_accounted_and_recorded() -> None:
    transport = DecisionsTransport()
    executor = _decision_executor(transport)
    assert executor_step_observation_of(executor) == "model_requests"
    handle = RunHandle()
    result = run_body(_judge, runner=make_runner(accepts_aggregate_executor_accounting=False), step_executor=executor, handle=handle)
    assert result.value == "True"
    run_entry = handle.ledger.entries[0].event
    assert isinstance(run_entry, RunEntryEvent) and run_entry.executor_step_observation == "model_requests"
    assert len(transport.request_body_list) == 1
    [outcome] = _outcome_list(handle)
    assert (outcome.block_kind, outcome.status, outcome.outcome_kind) == ("decide", "success", "pass")
    assert dict(outcome.committed_binding_name_to_value) == {"supported": True}
    assert outcome.response_model_name_tuple == ("gpt-6-luna",)
    assert outcome.executor_step_count == outcome.executor_usage.request_count == 1
    assert outcome.executor_usage.total_token_count == 12


class _RefusingExecutorSteps:
    identity = "refusing-executor-steps"

    def admit(self, request: ResourceRequest) -> ResourceRejection | None:
        return ResourceRejection(reason="no executor steps") if request.kind == "executor_step" else None

    def record(self, consumption: ResourceConsumption) -> None:
        pass


def test_executor_step_admission_precedes_the_decision_request() -> None:
    transport = DecisionsTransport()
    runner = make_runner(accepts_aggregate_executor_accounting=False, allocation_factory=lambda run_identity: _RefusingExecutorSteps())

    def body(run_input: EchoInput) -> EchoResult:
        try:
            _supported(run_input.text)
        except NaturalExecutionError as failure:
            return EchoResult(value=failure.failure_kind)
        return EchoResult(value="admitted")

    handle = RunHandle()
    assert run_body(body, runner=runner, step_executor=_decision_executor(transport), handle=handle).value == "allocation_exhausted"
    assert transport.request_body_list == []
    exhaustion = handle.ledger.select(ResourceExhaustionEvent)[0].event
    assert isinstance(exhaustion, ResourceExhaustionEvent) and exhaustion.request_kind == "executor_step"
    [outcome] = _outcome_list(handle)
    assert (outcome.block_kind, outcome.status, outcome.failure_kind) == ("decide", "failure", "allocation_exhausted")


def test_a_decision_without_a_decision_model_is_a_recorded_decide_failure() -> None:
    handle = RunHandle()
    with pytest.raises(RunFailedError):
        run_body(_judge, runner=make_runner(accepts_aggregate_executor_accounting=False), step_executor=AgentStepExecutor(), handle=handle)
    [outcome] = _outcome_list(handle)
    assert (outcome.block_kind, outcome.status, outcome.executor_usage.request_count) == ("decide", "failure", 0)


def test_custom_executors_receive_decide_requests_with_only_the_pass_outcome() -> None:
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"supported": False})])
    handle = RunHandle()
    assert run_body(_judge, runner=make_runner(), step_executor=executor, handle=handle).value == "False"
    [call] = executor.requests
    assert isinstance(call, StepRequest)
    assert (call.block_kind, call.allowed_outcome_kinds, call.output_binding_names) == ("decide", ("pass",), ("supported",))
    assert call.input_binding_name_to_value == {"text": "input"}
    [outcome] = _outcome_list(handle)
    assert (outcome.block_kind, outcome.outcome_kind) == ("decide", "pass")
