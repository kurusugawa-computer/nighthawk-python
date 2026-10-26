from __future__ import annotations

import asyncio
from typing import Annotated, Any

import pytest
from pydantic import BeforeValidator, ValidationError
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.backends.tool_bridge import execute_tool_call
from nighthawk.lifecycle import StepFailed, StepFinished, StepLifecycle
from nighthawk.runtime import OperationFinished
from nighthawk.tools.execution import ToolResultWrapperToolset
from tests.support.executors import StubExecutor
from tests.support.prompts import build_step_context


@nh.function
async def run_validated_action() -> None:
    """execute
    Call action once, then finish.
    """


@pytest.mark.parametrize("validator_kind", ["schema", "arguments"])
def test_native_validator_python_exception_preserves_identity_and_admission(validator_kind: str) -> None:
    original = TypeError("application validator failed")
    cause = RuntimeError("application cause")
    original.__cause__ = cause
    validator_calls = 0
    body_calls = 0
    model_calls = 0
    operations: list[OperationFinished] = []
    terminals: list[StepFinished] = []

    def validate(value: Any) -> Any:
        nonlocal validator_calls
        validator_calls += 1
        raise original

    async def validate_arguments(context: Any, value: int) -> None:
        validate(value)

    async def action(value: int) -> int:
        nonlocal body_calls
        body_calls += 1
        return value

    if validator_kind == "schema":
        action.__annotations__["value"] = Annotated[int, BeforeValidator(validate)]
    tool = Tool(action, args_validator=validate_arguments if validator_kind == "arguments" else None)

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        return ModelResponse(parts=[ToolCallPart("action", {"value": 1}, "failed-admission")])

    executor = nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with (
        nh.run(executor),
        nh.scope(tools=[tool], lifecycle=StepLifecycle(on_operation_finished=operations.append, on_step_finished=terminals.append)),
        pytest.raises(TypeError) as caught,
    ):
        asyncio.run(run_validated_action())

    assert caught.value is original
    assert caught.value.__cause__ is cause
    assert validator_calls == model_calls == 1
    assert body_calls == 0
    assert len(operations) == len(terminals) == 1
    assert operations[0].request.operation_id
    assert operations[0].request.kind == "native_tool"
    assert operations[0].request.argument_name_to_value == {"value": 1}
    assert operations[0].phase == "admission"
    assert operations[0].evaluation_started is False
    assert operations[0].original_exception is original
    assert isinstance(terminals[0], StepFailed)
    assert terminals[0].original_exception is original


@pytest.mark.parametrize("failure_kind", ["schema", "python", "retry", "approval", "deferred"])
def test_backend_native_validation_preserves_records_and_control_signals(failure_kind: str) -> None:
    context = build_step_context(python_globals={}, python_locals={})
    operations: list[OperationFinished] = []
    body_calls = 0
    original = (
        TypeError("validator failed")
        if failure_kind == "python"
        else ModelRetry("Use another value")
        if failure_kind == "retry"
        else ApprovalRequired()
        if failure_kind == "approval"
        else CallDeferred()
    )

    async def action(value: int) -> int:
        nonlocal body_calls
        body_calls += 1
        return value

    async def validate_arguments(context: Any, value: int) -> None:
        raise original

    async def execute() -> Any:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        tool = Tool(action, args_validator=None if failure_kind == "schema" else validate_arguments)
        toolset = ToolResultWrapperToolset(FunctionToolset([tool]))
        tool_name_to_tool = await toolset.get_tools(run_context)
        return await execute_tool_call(
            tool_name="action",
            tool=tool_name_to_tool["action"],
            toolset=toolset,
            arguments={"value": "invalid" if failure_kind == "schema" else 1},
            run_context=run_context,
            tool_call_id="admission",
        )

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(on_operation_finished=operations.append)):
        if failure_kind in ("schema", "retry"):
            result = asyncio.run(execute())
            assert result["kind"] == "retry_prompt"
            assert context.operation_failure is None
        else:
            with pytest.raises(type(original)) as caught:
                asyncio.run(execute())
            assert caught.value is original

    assert body_calls == 0
    if failure_kind in ("approval", "deferred"):
        assert context.operation_failure is None
        assert not operations
    else:
        assert len(operations) == 1
        assert operations[0].phase == "admission"
        assert operations[0].evaluation_started is False
        if failure_kind == "schema":
            assert isinstance(operations[0].original_exception, ValidationError)
        else:
            assert operations[0].original_exception is original
        if failure_kind == "python":
            assert context.operation_failure is original


@pytest.mark.parametrize(
    ("tool_name", "arguments", "operation_kind"),
    [("nh_evaluate", {}, "evaluation"), ("nh_assign", {"target_path": "answer"}, "assignment"), ("action", '{"value":', "native_tool")],
)
def test_invalid_tool_request_retains_failed_admission(tool_name: str, arguments: Any, operation_kind: str) -> None:
    operations: list[OperationFinished] = []
    model_calls = 0

    async def action(value: int) -> None:
        raise AssertionError("Invalid arguments cannot dispatch the body")

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name, arguments, "invalid")])
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])

    executor = nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with nh.run(executor), nh.scope(tools=[Tool(action)], lifecycle=StepLifecycle(on_operation_finished=operations.append)):
        asyncio.run(run_validated_action())
    assert model_calls == 2
    assert len(operations) == 1
    assert operations[0].request.kind == operation_kind
    assert operations[0].request.tool_name == tool_name
    assert operations[0].request.argument_name_to_value == (arguments if isinstance(arguments, dict) else {"unvalidated_arguments": arguments})
    assert operations[0].phase == "admission"
    assert operations[0].evaluation_started is False
    assert isinstance(operations[0].original_exception, ValidationError)


def test_capability_recovery_dispatches_once_after_recording_failed_admission() -> None:
    operations: list[OperationFinished] = []
    body_calls: list[int] = []
    model_calls = 0
    recovered_errors: list[Exception] = []

    async def action(value: int) -> int:
        body_calls.append(value)
        return value

    async def recover(context: Any, *, call: Any, tool_def: Any, args: Any, error: Any) -> Any:
        assert len(operations) == 1
        assert operations[0].original_exception is error
        recovered_errors.append(error)
        return {"value": 7}

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(parts=[ToolCallPart("action", {"value": "invalid"}, "recovered")])
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])

    executor = nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with (
        nh.run(executor),
        nh.scope(
            tools=[Tool(action)], capabilities=[Hooks(tool_validate_error=recover)], lifecycle=StepLifecycle(on_operation_finished=operations.append)
        ),
    ):
        asyncio.run(run_validated_action())
    assert model_calls == 2
    assert body_calls == [7]
    assert len(recovered_errors) == 1
    assert len(operations) == 2
    assert operations[0].phase == "admission"
    assert operations[0].request.argument_name_to_value == {"value": "invalid"}
    assert operations[1].status == "succeeded"
    assert operations[1].request.argument_name_to_value == {"value": 7}
    assert operations[0].request.operation_id != operations[1].request.operation_id
