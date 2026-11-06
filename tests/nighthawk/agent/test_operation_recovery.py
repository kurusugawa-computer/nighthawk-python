"""Recoverable operation refusals preserve one host execution and its limits."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Literal, cast

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import nighthawk as nh
from nighthawk.agent import (
    Grant,
    NaturalExecutionOutcomeEvent,
    RunEndEvent,
    RunEntryEvent,
    RunFailedError,
    RunHandle,
    TraceAnomalyEvent,
    current_grant,
)
from nighthawk.agent.runtime import AsyncScaffoldComposition, RunContract, Runner, ScaffoldComposition, bounded_resource_allocation
from nighthawk.runtime.expression import evaluate_expression, evaluate_expression_async
from nighthawk.runtime.operation_contract import OperationRejectedError
from nighthawk.runtime.step_outcome import Pass
from nighthawk.runtime.step_request import StepCandidate, StepRequest

from .conftest import make_runner


@nh.function
def _calculate(effects: list[str]) -> int:
    result: int = 0
    """execute
    Choose a permitted operation with <effects> and assign <:result>.
    """
    return result


@nh.function
async def _calculate_async(effects: list[str]) -> int:
    result: int = 0
    """execute
    Choose a permitted operation with <effects> and assign <:result>.
    """
    return result


async def _execute(runner: Runner, handle: RunHandle, effects: list[str], *, asynchronous: bool) -> int:
    grant = Grant.of(shared=effects)
    if asynchronous:
        return await runner.run(
            AsyncScaffoldComposition(
                RunContract(type(None), int),
                lambda value: _calculate_async(effects),
                run_entry_grant=grant,
                function_definition_tuple=(_calculate_async,),
            ),
            None,
            handle=handle,
        )
    return runner.run(
        ScaffoldComposition(
            RunContract(type(None), int),
            lambda value: _calculate(effects),
            run_entry_grant=grant,
            function_definition_tuple=(_calculate,),
        ),
        None,
        handle=handle,
    )


def _single_outcome(handle: RunHandle) -> NaturalExecutionOutcomeEvent:
    assert len(handle.ledger.select(RunEntryEvent)) == len(handle.ledger.select(RunEndEvent)) == 1
    outcomes = handle.ledger.select(NaturalExecutionOutcomeEvent)
    assert len(outcomes) == 1
    outcome = outcomes[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    return outcome


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_custom_executor_uses_refusal_feedback_without_restarting(asynchronous: bool, runner: Runner) -> None:
    refusals: list[OperationRejectedError] = []
    effects: list[str] = []

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            evaluate_expression(request.step_context, "effects.append('before')")
            with pytest.raises(OperationRejectedError) as caught:
                evaluate_expression(request.step_context, "eval(\"effects.append('forbidden')\")")
            refusals.append(caught.value)
            value = evaluate_expression(request.step_context, "len(effects) + 6")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": value})

        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            await evaluate_expression_async(request.step_context, "effects.append('before')")
            with pytest.raises(OperationRejectedError) as caught:
                await evaluate_expression_async(request.step_context, "eval(\"effects.append('forbidden')\")")
            refusals.append(caught.value)
            value = await evaluate_expression_async(request.step_context, "len(effects) + 6")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": value})

    handle = RunHandle()
    assert await _execute(replace(runner, step_executor=Executor()), handle, effects, asynchronous=asynchronous) == 7
    assert effects == ["before"]
    assert len(refusals) == 1
    rejection = refusals[0].rejection
    assert "raw dynamic execution" in rejection.reason
    assert rejection.guidance and "ordinary Python expressions" in rejection.guidance
    assert rejection.model_feedback().get("evaluation_started") is False
    outcome = _single_outcome(handle)
    assert outcome.status == "success"
    assert rejection.request.execution_reference.run_id == handle.run_id
    assert outcome.committed_binding_name_to_value == {"result": 7}
    assert len(handle.ledger.select(TraceAnomalyEvent)) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("completion", ["success", "allocation_exhausted", "host_termination"])
async def test_model_feedback_preserves_state_authority_and_request_limits(
    asynchronous: bool, completion: Literal["success", "allocation_exhausted", "host_termination"]
) -> None:
    handle = RunHandle()
    effects: list[str] = []
    references: list[nh.ExecutionReference] = []
    grants: list[Grant] = []

    async def respond(messages: list[ModelMessage], information: AgentInfo) -> ModelResponse:
        references.append(nh.get_scope().execution_reference)
        grants.append(current_grant())
        assert grants[-1].references["shared"] is effects
        assert grants[-1] is grants[0]
        output_name = information.output_tools[0].name
        if len(references) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("nh_evaluate", {"expression": "effects.append('before')"}, "before"),
                    ToolCallPart("nh_evaluate", {"expression": "eval(\"effects.append('forbidden')\")"}, "refused"),
                    ToolCallPart("nh_evaluate", {"expression": "effects.append('skipped')"}, "skipped"),
                    ToolCallPart(output_name, {"result": {"kind": "pass"}}, "discarded-output"),
                ]
            )
        assert len(references) == 2
        feedback = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        refusal = next(part for part in feedback if part.tool_call_id == "refused")
        assert refusal.outcome == "denied"
        feedback_name_to_value = cast(dict[str, object], refusal.content if isinstance(refusal.content, dict) else json.loads(str(refusal.content)))
        assert "raw dynamic execution" in str(feedback_name_to_value)
        assert "ordinary Python expressions" in str(feedback_name_to_value)
        assert feedback_name_to_value["evaluation_started"] is False
        assert feedback_name_to_value["step_execution_id"] == references[0].step_execution_id
        assert "skipped" in str([part.content for part in feedback if part.tool_call_id == "skipped"])
        assert effects == ["before"]
        if completion == "allocation_exhausted":
            return ModelResponse(parts=[ToolCallPart("nh_evaluate", {"expression": "eval('7')"}, "refused-again")])
        if completion == "host_termination":
            handle.terminate("Stop after receiving the refusal")
        return ModelResponse(
            parts=[
                ToolCallPart("nh_assign", {"target_path": "result", "expression": "len(effects) + 6"}, "corrected"),
                ToolCallPart(output_name, {"result": {"kind": "pass"}}, "finish"),
            ]
        )

    executor = nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    runner = replace(
        make_runner(
            accepts_aggregate_executor_accounting=False,
            allocation_factory=lambda identity: bounded_resource_allocation(maximum_executor_step_count=2),
        ),
        step_executor=executor,
    )
    if completion == "success":
        assert await _execute(runner, handle, effects, asynchronous=asynchronous) == 7
    else:
        with pytest.raises(RunFailedError):
            await _execute(runner, handle, effects, asynchronous=asynchronous)
    assert effects == ["before"]
    assert len(references) == 2
    assert references[0] == references[1]
    outcome = _single_outcome(handle)
    assert references[0].run_id == handle.run_id
    if completion == "success":
        assert outcome.status == "success"
        assert outcome.committed_binding_name_to_value == {"result": 7}
        assert outcome.executor_usage.request_count == outcome.executor_step_count == 2
    else:
        assert outcome.status == "failure"
        assert not outcome.committed_binding_name_to_value
        assert outcome.failure_kind == ("terminated" if completion == "host_termination" else "allocation_exhausted")
        if completion == "allocation_exhausted":
            assert outcome.executor_usage.request_count == outcome.executor_step_count == 2
    anomalies = [entry.event for entry in handle.ledger.select(TraceAnomalyEvent)]
    refusals = [event for event in anomalies if isinstance(event, TraceAnomalyEvent) and event.anomaly_kind == "expression_raw_dynamic_execution"]
    assert len(refusals) == (2 if completion == "allocation_exhausted" else 1)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_explicit_abort_cannot_be_recovered_as_a_success(asynchronous: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    from nighthawk.agent import NaturalExecutionError
    from nighthawk.agent._host import Host
    from nighthawk.oversight import AbortStep, StepAbortedError

    monkeypatch.setattr(Host, "_inspect_operation", lambda self, request: AbortStep("Host policy ends this step"))
    effects: list[str] = []

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            with pytest.raises(StepAbortedError):
                await evaluate_expression_async(request.step_context, "effects.append('forbidden')")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 7})

    handle = RunHandle()
    with pytest.raises(RunFailedError) as caught:
        await _execute(replace(make_runner(), step_executor=Executor()), handle, effects, asynchronous=asynchronous)
    failure = caught.value.__cause__
    assert isinstance(failure, NaturalExecutionError)
    assert isinstance(failure.__cause__, StepAbortedError)
    assert effects == []
    outcome = _single_outcome(handle)
    assert outcome.status == "failure" and outcome.failure_kind == "commit_failed"
    assert not outcome.committed_binding_name_to_value
