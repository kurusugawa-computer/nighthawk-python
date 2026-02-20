from __future__ import annotations

import json

import anyio
import pytest
from opentelemetry import context as otel_context
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
from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.contracts import ToolResultWrapperToolset
from nighthawk.tools.mcp_boundary import call_tool_for_claude_agent_sdk, call_tool_for_low_level_mcp_server
from nighthawk.tools.registry import get_visible_tools, reset_global_tools_for_tests


@pytest.fixture(autouse=True)
def _reset_tools() -> None:
    reset_global_tools_for_tests()


def _new_step_context() -> StepContext:
    return StepContext(
        step_id="test_tool_boundary",
        run_configuration=nh.RunConfiguration(),
        step_globals={"__builtins__": __builtins__},
        step_locals={},
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
        deps_type=StepContext,
        output_type=str,
        toolsets=[toolset],
    )

    result = agent.run_sync("hi", deps=_new_step_context())

    tool_return_parts = _tool_return_parts(result)
    assert len(tool_return_parts) == 1

    tool_return_part = tool_return_parts[0]
    content = getattr(tool_return_part, "content")

    dumped = tool_return_ta.dump_python(content, mode="json")
    assert dumped["error"] is None
    assert dumped["value"] == "<nonserializable>"


def test_wrapper_converts_tool_exception_to_toolresult_failure() -> None:
    @nh.tool(name="test_raises")
    def test_raises(run_context) -> object:  # type: ignore[no-untyped-def]
        _ = run_context
        raise RuntimeError("boom")

    toolset = ToolResultWrapperToolset(FunctionToolset(get_visible_tools()))

    agent = Agent(
        model=TestModel(call_tools=["test_raises"], custom_output_text="ok"),
        deps_type=StepContext,
        output_type=str,
        toolsets=[toolset],
    )

    result = agent.run_sync("hi", deps=_new_step_context())

    tool_return_parts = _tool_return_parts(result)
    assert len(tool_return_parts) == 1

    tool_return_part = tool_return_parts[0]
    content = getattr(tool_return_part, "content")

    dumped = tool_return_ta.dump_python(content, mode="json")
    assert dumped["value"] is None
    assert dumped["error"]["kind"] == "internal"


def test_backend_handler_invalid_args_returns_retry_prompt_text() -> None:
    @nh.tool(name="test_arg")
    def test_arg(run_context, *, x: int) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return x

    step_context = _new_step_context()

    run_context = RunContext(
        deps=step_context,
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


def test_mcp_boundary_low_level_mcp_server_returns_text_content_and_propagates_otel_context() -> None:
    parent_otel_context = otel_context.set_value("nighthawk.test", "1", otel_context.get_current())

    async def tool_handler(arguments: dict[str, object]) -> str:
        assert arguments == {"x": 1}
        assert otel_context.get_value("nighthawk.test") == "1"
        return '{"value":2,"error":null}'

    async def call_boundary() -> list[object]:
        return await call_tool_for_low_level_mcp_server(
            tool_name="stub_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=parent_otel_context,
        )

    content = anyio.run(call_boundary)
    assert isinstance(content, list)
    assert len(content) == 1
    assert getattr(content[0], "type") == "text"
    assert getattr(content[0], "text") == '{"value":2,"error":null}'


def test_mcp_boundary_claude_agent_sdk_returns_text_content() -> None:
    async def tool_handler(arguments: dict[str, object]) -> str:
        assert arguments == {"x": 1}
        return '{"value":2,"error":null}'

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_agent_sdk(
            tool_name="stub_tool",
            arguments={"x": 1},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    assert response == {"content": [{"type": "text", "text": '{"value":2,"error":null}'}]}


def test_mcp_boundary_converts_boundary_exception_to_json_failure_text_without_run_context() -> None:
    async def tool_handler(arguments: dict[str, object]) -> str:
        _ = arguments
        raise RuntimeError("boom")

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_agent_sdk(
            tool_name="stub_tool",
            arguments={},
            tool_handler=tool_handler,
            parent_otel_context=otel_context.get_current(),
        )

    response = anyio.run(call_boundary)
    content = response["content"]
    assert isinstance(content, list)
    assert len(content) == 1
    text = content[0]["text"]

    parsed = json.loads(text)
    assert parsed["value"] is None
    assert parsed["error"]["kind"] == "internal"
