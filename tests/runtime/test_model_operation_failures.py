from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import Tool
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import nighthawk as nh
from nighthawk.lifecycle import StepFailed, StepFinished, StepLifecycle
from nighthawk.runtime import OperationFinished


@nh.function
async def run_action() -> None:
    """execute
    Call action once, then finish.
    """


@pytest.mark.parametrize("route", ["native", "evaluation"])
@pytest.mark.parametrize("hook_behavior", ["propagate", "recover", "replace"])
def test_application_validation_failure_stops_before_another_model_request(route: str, hook_behavior: str) -> None:
    with pytest.raises(ValidationError) as captured:
        TypeAdapter(int).validate_python("invalid application data")
    original = captured.value
    original_cause = ValueError("original application cause")
    original.__cause__ = original_cause
    body_calls = 0
    model_calls = 0
    operations: list[OperationFinished] = []
    terminals: list[StepFinished] = []
    hook_errors: list[Exception] = []

    async def action() -> int:
        nonlocal body_calls
        body_calls += 1
        raise original

    async def on_error(context: Any, *, call: Any, tool_def: Any, args: Any, error: Exception) -> object:
        hook_errors.append(error)
        if hook_behavior == "recover":
            return 42
        if hook_behavior == "replace":
            raise RuntimeError("capability replaced the application exception")
        raise error

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            selected = (
                ToolCallPart("action", {}, "operation") if route == "native" else ToolCallPart("nh_evaluate", {"expression": "action()"}, "operation")
            )
            return ModelResponse(parts=[selected])
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])

    executor = nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with (
        nh.run(executor),
        nh.scope(
            tools=[Tool(action, max_retries=2)] if route == "native" else [],
            task_references={"action": action},
            capabilities=[Hooks(tool_execute_error=on_error)],
            lifecycle=StepLifecycle(on_operation_finished=operations.append, on_step_finished=terminals.append),
        ),
        pytest.raises(ValidationError) as caught,
    ):
        asyncio.run(run_action())

    assert caught.value is original
    assert caught.value.__cause__ is original_cause
    assert body_calls == model_calls == 1
    assert hook_errors == [original]
    assert len(operations) == len(terminals) == 1
    assert operations[0].original_exception is original
    assert operations[0].evaluation_started is True
    assert isinstance(terminals[0], StepFailed)
    assert terminals[0].failure_stage == "operation_execution"
    assert terminals[0].original_exception is original


@pytest.mark.parametrize("failure_origin", ["arguments", "explicit_retry"])
def test_model_input_failure_and_explicit_retry_remain_recoverable(failure_origin: str) -> None:
    model_calls = 0
    body_calls: list[int] = []
    operations: list[OperationFinished] = []

    async def action(value: int) -> int:
        body_calls.append(value)
        if failure_origin == "explicit_retry" and value == 0:
            raise ModelRetry("Use 1 for this synthetic domain operation")
        return value

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(parts=[ToolCallPart("action", {"value": "invalid" if failure_origin == "arguments" else 0}, "first")])
        if model_calls == 2:
            assert any(isinstance(part, RetryPromptPart) for message in messages for part in message.parts)
            return ModelResponse(parts=[ToolCallPart("action", {"value": 1}, "second")])
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])

    executor = nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with (
        nh.run(executor),
        nh.scope(tools=[Tool(action, max_retries=2)], capabilities=[Hooks()], lifecycle=StepLifecycle(on_operation_finished=operations.append)),
    ):
        asyncio.run(run_action())
    assert model_calls == 3
    assert body_calls == ([1] if failure_origin == "arguments" else [0, 1])
    assert len(operations) == 2
    assert operations[0].phase == ("admission" if failure_origin == "arguments" else "evaluation")
    assert operations[0].evaluation_started is (failure_origin != "arguments")
    assert operations[-1].status == "succeeded"
