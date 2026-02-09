from __future__ import annotations

import anyio
import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.messages import tool_return_ta
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.backends import build_tool_name_to_handler
from nighthawk.execution.context import ExecutionContext
from nighthawk.tools.contracts import ToolResultWrapperToolset
from nighthawk.tools.registry import get_visible_tools, reset_global_tools_for_tests


@pytest.fixture(autouse=True)
def _reset_tools() -> None:
    reset_global_tools_for_tests()


def _new_execution_context() -> ExecutionContext:
    return ExecutionContext(
        execution_id="test_tool_boundary",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals={"__builtins__": __builtins__},
        execution_locals={},
        binding_commit_targets=set(),
    )


def _tool_return_parts(result) -> list[object]:  # type: ignore[no-untyped-def]
    messages = result.all_messages()
    parts: list[object] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", None) == "tool-return":
                parts.append(part)
    return parts


def test_wrapper_returns_toolresult_success_with_placeholder_for_unknown_value() -> None:
    class NotJson:
        pass

    @nh.tool(name="test_unknown_return")
    def test_unknown_return(run_context) -> object:  # type: ignore[no-untyped-def]
        _ = run_context
        return NotJson()

    toolset = ToolResultWrapperToolset(FunctionToolset(get_visible_tools()))

    agent = Agent(
        model=TestModel(call_tools=["test_unknown_return"], custom_output_text="ok"),
        deps_type=ExecutionContext,
        output_type=str,
        toolsets=[toolset],
    )

    result = agent.run_sync("hi", deps=_new_execution_context())

    tool_return_parts = _tool_return_parts(result)
    assert len(tool_return_parts) == 1

    tool_return_part = tool_return_parts[0]
    content = getattr(tool_return_part, "content")

    dumped = tool_return_ta.dump_python(content, mode="json")
    assert dumped["status"] == "success"
    assert dumped["error"] is None
    assert dumped["value"] == "<NotJson>"


def test_wrapper_converts_tool_exception_to_toolresult_failure() -> None:
    @nh.tool(name="test_raises")
    def test_raises(run_context) -> object:  # type: ignore[no-untyped-def]
        _ = run_context
        raise RuntimeError("boom")

    toolset = ToolResultWrapperToolset(FunctionToolset(get_visible_tools()))

    agent = Agent(
        model=TestModel(call_tools=["test_raises"], custom_output_text="ok"),
        deps_type=ExecutionContext,
        output_type=str,
        toolsets=[toolset],
    )

    result = agent.run_sync("hi", deps=_new_execution_context())

    tool_return_parts = _tool_return_parts(result)
    assert len(tool_return_parts) == 1

    tool_return_part = tool_return_parts[0]
    content = getattr(tool_return_part, "content")

    dumped = tool_return_ta.dump_python(content, mode="json")
    assert dumped["status"] == "failure"
    assert dumped["value"] is None
    assert dumped["error"]["kind"] == "internal"


def test_backend_handler_invalid_args_returns_retry_prompt_text() -> None:
    @nh.tool(name="test_arg")
    def test_arg(run_context, *, x: int) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return x

    execution_context = _new_execution_context()

    run_context = RunContext(
        deps=execution_context,
        model=TestModel(),
        usage=RunUsage(),
    )

    visible_tools = get_visible_tools()

    base_toolset = FunctionToolset(visible_tools)

    async def get_tool_def():  # type: ignore[no-untyped-def]
        tool_name_to_tool = await base_toolset.get_tools(run_context)
        return tool_name_to_tool["test_arg"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
                backend_label="test",
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_arg"]

    async def call_invalid() -> str:
        with set_current_run_context(run_context):
            return await handler({"x": "not an int"})

    retry_text = anyio.run(call_invalid)
    assert isinstance(retry_text, str)
    assert "Fix the errors and try again." in retry_text
