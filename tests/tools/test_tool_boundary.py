from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any, cast

import anyio
import pytest
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent, RunContext
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.messages import ToolReturnPart, tool_return_ta
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.backends.mcp_boundary import call_tool_for_claude_code_sdk, call_tool_for_low_level_mcp_server
from nighthawk.backends.tool_bridge import build_tool_name_to_handler
from nighthawk.errors import NighthawkError
from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.contracts import ToolBoundaryError
from nighthawk.tools.execution import ToolResultWrapperToolset
from nighthawk.tools.registry import _reset_all_tools_for_tests, get_visible_tools
from tests.execution.stub_executor import StubExecutor


@pytest.fixture(autouse=True)
def _reset_tools() -> None:
    _reset_all_tools_for_tests()


def _new_step_context() -> StepContext:
    return StepContext(
        step_id="test_tool_boundary",
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        implicit_reference_name_to_value={},
    )


@pytest.fixture
def tool_span_exporter() -> Generator[tuple[InMemorySpanExporter, TracerProvider], None, None]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    yield span_exporter, tracer_provider


def _tool_return_parts(result) -> list[ToolReturnPart]:  # type: ignore[no-untyped-def]
    messages = result.all_messages()
    parts: list[ToolReturnPart] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart):
                parts.append(part)
    return parts


def _get_finished_tool_spans(tool_span_exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span_data for span_data in tool_span_exporter.get_finished_spans() if (span_data.attributes or {}).get("gen_ai.tool.name") is not None]


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
    content = tool_return_part.content

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
    content = tool_return_part.content

    dumped = tool_return_ta.dump_python(content, mode="json")
    assert dumped["value"] is None
    assert dumped["error"]["kind"] == "internal"


def test_wrapper_converts_oversight_rejection_to_toolresult_failure() -> None:
    tool_call_count = 0

    @nh.tool(name="test_denied_tool")
    def test_denied_tool(run_context) -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        _ = run_context
        tool_call_count += 1
        return 1

    def reject_tool_call(tool_call: nh.oversight.ToolCall) -> nh.oversight.Reject:
        assert tool_call.tool_name == "test_denied_tool"
        return nh.oversight.Reject("human rejected tool")

    toolset = ToolResultWrapperToolset(FunctionToolset(get_visible_tools()))

    agent = Agent(
        model=TestModel(call_tools=["test_denied_tool"], custom_output_text="ok"),
        deps_type=StepContext,
        output_type=str,
        toolsets=[toolset],
    )

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=reject_tool_call)):
        result = agent.run_sync("hi", deps=_new_step_context())

    tool_return_parts = _tool_return_parts(result)
    assert len(tool_return_parts) == 1
    dumped = tool_return_ta.dump_python(tool_return_parts[0].content, mode="json")
    assert dumped["value"] is None
    assert dumped["error"]["kind"] == "oversight"
    assert dumped["error"]["message"] == "human rejected tool"
    assert tool_call_count == 0


def test_backend_handler_wraps_recoverable_tool_boundary_error() -> None:
    @nh.tool(name="test_boundary_error")
    def test_boundary_error() -> int:  # type: ignore[no-untyped-def]
        raise ToolBoundaryError(
            kind="execution",
            message="tool crashed",
            guidance="Retry with different arguments.",
        )

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
        return tool_name_to_tool["test_boundary_error"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_boundary_error"]

    async def call_handler() -> str:
        with set_current_run_context(run_context):
            return await handler({})

    result_text = anyio.run(call_handler)
    parsed = json.loads(result_text)
    assert parsed["value"] is None
    assert parsed["error"]["kind"] == "execution"
    assert parsed["error"]["message"] == "tool crashed"
    assert parsed["error"]["guidance"] == "Retry with different arguments."


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
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_arg"]

    async def call_invalid() -> str:
        with set_current_run_context(run_context):
            return await handler({"x": "not an int"})

    retry_text = anyio.run(call_invalid)
    assert isinstance(retry_text, str)
    assert "Fix the errors and try again." in retry_text


def test_backend_handler_calls_oversight_once_for_accept_decision(
    tool_span_exporter: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    span_exporter, tracer_provider = tool_span_exporter
    inspection_count = 0

    @nh.tool(name="test_once_tool")
    def test_once_tool() -> int:  # type: ignore[no-untyped-def]
        return 3

    def accept_tool_call(_tool_call: nh.oversight.ToolCall) -> nh.oversight.Accept:
        nonlocal inspection_count
        inspection_count += 1
        return nh.oversight.Accept()

    step_context = _new_step_context()
    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
        tracer=tracer_provider.get_tracer("tool-tests"),
    )

    visible_tools = get_visible_tools()
    base_toolset = FunctionToolset(visible_tools)

    async def get_tool_def():  # type: ignore[no-untyped-def]
        tool_name_to_tool = await base_toolset.get_tools(run_context)
        return tool_name_to_tool["test_once_tool"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_once_tool"]

    async def call_handler() -> str:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=accept_tool_call)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    result_text = anyio.run(call_handler)
    parsed = json.loads(result_text)
    assert parsed["value"] == 3
    assert parsed["error"] is None
    assert inspection_count == 1

    tool_spans = _get_finished_tool_spans(span_exporter)
    assert len(tool_spans) == 1
    oversight_event = next(event for event in tool_spans[0].events if event.name == "nighthawk.oversight.decision")
    oversight_attributes = dict(oversight_event.attributes or {})
    assert oversight_attributes["nighthawk.oversight.subject"] == "tool_call"
    assert oversight_attributes["nighthawk.oversight.verdict"] == "accept"
    assert oversight_attributes["tool.name"] == "test_once_tool"
    assert oversight_attributes["run.id"]
    assert oversight_attributes["scope.id"]
    assert oversight_attributes["step.id"] == "test_tool_boundary"


def test_backend_handler_raises_nighthawk_error_for_invalid_tool_oversight_decision() -> None:
    @nh.tool(name="test_invalid_tool_decision")
    def test_invalid_tool_decision() -> int:  # type: ignore[no-untyped-def]
        return 5

    def invalid_tool_decision(_tool_call: nh.oversight.ToolCall) -> nh.oversight.ToolCallDecision:
        return cast(Any, "bad")

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
        return tool_name_to_tool["test_invalid_tool_decision"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_invalid_tool_decision"]

    async def call_handler() -> str:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=invalid_tool_decision)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    with pytest.raises(NighthawkError, match="must return Accept or Reject"):
        anyio.run(call_handler)


def test_backend_handler_preserves_oversight_rejection_and_records_tool_span_event(
    tool_span_exporter: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    span_exporter, tracer_provider = tool_span_exporter
    tool_call_count = 0

    @nh.tool(name="test_governed_tool")
    def test_governed_tool() -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        tool_call_count += 1
        return 7

    def reject_tool_call(tool_call: nh.oversight.ToolCall) -> nh.oversight.Reject:
        assert tool_call.argument_name_to_value == {}
        return nh.oversight.Reject("needs oversight")

    step_context = _new_step_context()
    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
        tracer=tracer_provider.get_tracer("tool-tests"),
    )

    visible_tools = get_visible_tools()
    base_toolset = FunctionToolset(visible_tools)

    async def get_tool_def():  # type: ignore[no-untyped-def]
        tool_name_to_tool = await base_toolset.get_tools(run_context)
        return tool_name_to_tool["test_governed_tool"].tool_def

    tool_def = anyio.run(get_tool_def)
    assert isinstance(tool_def, ToolDefinition)
    model_request_parameters = ModelRequestParameters(function_tools=[tool_def])

    async def build_handlers():  # type: ignore[no-untyped-def]
        with set_current_run_context(run_context):
            return await build_tool_name_to_handler(
                model_request_parameters=model_request_parameters,
                visible_tools=visible_tools,
            )

    handlers = anyio.run(build_handlers)
    handler = handlers["test_governed_tool"]

    async def call_handler() -> str:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_tool_call=reject_tool_call)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    result_text = anyio.run(call_handler)
    parsed = json.loads(result_text)
    assert parsed["value"] is None
    assert parsed["error"]["kind"] == "oversight"
    assert parsed["error"]["message"] == "needs oversight"
    assert tool_call_count == 0

    tool_spans = _get_finished_tool_spans(span_exporter)
    assert len(tool_spans) == 1
    oversight_event = next(event for event in tool_spans[0].events if event.name == "nighthawk.oversight.decision")
    oversight_attributes = dict(oversight_event.attributes or {})
    assert oversight_attributes["nighthawk.oversight.subject"] == "tool_call"
    assert oversight_attributes["nighthawk.oversight.verdict"] == "reject"
    assert oversight_attributes["tool.name"] == "test_governed_tool"
    assert oversight_attributes["nighthawk.oversight.reason"] == "needs oversight"
    assert oversight_attributes["run.id"]
    assert oversight_attributes["scope.id"]
    assert oversight_attributes["step.id"] == "test_tool_boundary"


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
    item: Any = content[0]
    assert item.type == "text"
    assert item.text == '{"value":2,"error":null}'


def test_mcp_boundary_claude_code_returns_text_content() -> None:
    async def tool_handler(arguments: dict[str, object]) -> str:
        assert arguments == {"x": 1}
        return '{"value":2,"error":null}'

    async def call_boundary() -> dict[str, object]:
        return await call_tool_for_claude_code_sdk(
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
        return await call_tool_for_claude_code_sdk(
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
