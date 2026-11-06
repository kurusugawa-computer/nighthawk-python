from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import Tool
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.core.lifecycle import StepCompleted, StepFinished, StepLifecycle, StepRaised
from nighthawk.core.operation_contract import OperationFinished


@nh.function
async def run_action() -> None:
    """execute
    Call action once, then finish.
    """


@pytest.mark.parametrize("route", ["native", "evaluation"])
@pytest.mark.parametrize("finish", ["pass", "raise"])
def test_application_exception_returns_to_the_model_which_selects_the_step_end(route: str, finish: str) -> None:
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
        raise error

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            selected = (
                ToolCallPart("action", {}, "operation") if route == "native" else ToolCallPart("nh_evaluate", {"expression": "action()"}, "operation")
            )
            return ModelResponse(parts=[selected])
        feedback = next(part.content for message in messages for part in message.parts if isinstance(part, ToolReturnPart))
        assert isinstance(feedback, dict)
        error = cast(dict[str, Any], feedback)["error"]
        assert error["kind"] == "exception" and error["status"] == "failed"
        assert error["message"].startswith("ValidationError:") and error["evaluation_started"] is True
        result: dict[str, object] = {"kind": "pass"}
        if finish == "raise":
            result = {"kind": "raise", "raise_message": "propagate", "raise_operation_id": error["operation_id"]}
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": result}, "finish")])

    executor = AgentStepExecutor(configuration=StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with (
        nh.run(executor),
        nh.scope(
            tools=[Tool(action, max_retries=2)] if route == "native" else [],
            task_references={"action": action},
            capabilities=[Hooks(tool_execute_error=on_error)],
            lifecycle=StepLifecycle(on_operation_finished=operations.append, on_step_finished=terminals.append),
        ),
    ):
        if finish == "raise":
            with pytest.raises(ValidationError) as caught:
                asyncio.run(run_action())
            assert caught.value is original and caught.value.__cause__ is original_cause
        else:
            asyncio.run(run_action())

    assert body_calls == 1 and model_calls == 2
    # Nighthawk reports the failed operation itself, so capability error hooks never see it.
    assert hook_errors == []
    assert len(operations) == len(terminals) == 1
    assert operations[0].status == "failed" and operations[0].original_exception is original
    assert operations[0].evaluation_started is True
    assert terminals[0].failed_operation_tuple == (operations[0],)
    if finish == "raise":
        assert isinstance(terminals[0], StepRaised) and terminals[0].exception is original
    else:
        assert isinstance(terminals[0], StepCompleted)


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
            assert any(isinstance(part, ToolReturnPart) and part.outcome == "retried" for message in messages for part in message.parts)
            return ModelResponse(parts=[ToolCallPart("action", {"value": 1}, "second")])
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])

    executor = AgentStepExecutor(configuration=StepExecutorConfiguration(execution_model=FunctionModel(respond)))
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


@pytest.mark.parametrize("failure", ["return_expression", "unknown_operation_id"])
def test_failed_control_result_returns_to_the_model_for_another_result(failure: str) -> None:
    model_calls = 0
    retry_text_list: list[str] = []

    @nh.function
    async def compute() -> int:
        """execute
        Return the number.
        """
        return 0

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        output_name = information.output_tools[0].name
        if model_calls == 1:
            result: dict[str, object] = (
                {"kind": "return", "return_expression": "1 / 0"}
                if failure == "return_expression"
                else {"kind": "raise", "raise_message": "propagate", "raise_operation_id": "unknown"}
            )
            return ModelResponse(parts=[ToolCallPart(output_name, {"result": result}, "first")])
        retry_text_list.extend(
            str(part.content) for message in messages for part in message.parts if isinstance(part, ToolReturnPart) and part.outcome == "retried"
        )
        return ModelResponse(parts=[ToolCallPart(output_name, {"result": {"kind": "return", "return_expression": "7"}}, "second")])

    executor = AgentStepExecutor(configuration=StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with nh.run(executor):
        assert asyncio.run(compute()) == 7
    assert model_calls == 2 and len(retry_text_list) == 1
    expected = "ZeroDivisionError: division by zero" if failure == "return_expression" else "No failed operation has raise_operation_id 'unknown'"
    assert expected in retry_text_list[0]
