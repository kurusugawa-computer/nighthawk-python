from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any, cast

import anyio
import pytest
import tiktoken
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import RunContext
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import Tool, ToolDefinition
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.backends.tool_bridge import build_tool_name_to_handler
from nighthawk.errors import NighthawkError
from nighthawk.tools.contracts import (
    ToolBoundaryError,
    ToolHandlerResult,
    render_tool_handler_result_preview_text,
)
from nighthawk.tools.declarations import get_visible_tools
from tests.support.executors import StubExecutor
from tests.support.tools import _new_step_context


@pytest.fixture
def tool_span_exporter() -> Generator[tuple[InMemorySpanExporter, TracerProvider]]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    yield span_exporter, tracer_provider


def _get_finished_tool_spans(tool_span_exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span_data for span_data in tool_span_exporter.get_finished_spans() if (span_data.attributes or {}).get("gen_ai.tool.name") is not None]


def _render_preview_text_for_test(tool_handler_result: ToolHandlerResult) -> str:
    return render_tool_handler_result_preview_text(
        tool_handler_result=tool_handler_result,
        max_tokens=1_200,
        encoding=tiktoken.get_encoding("o200k_base"),
        style="default",
    )


def test_backend_handler_propagates_application_boundary_error() -> None:
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

    with nh.run(StubExecutor()), nh.scope(tools=[Tool(test_boundary_error, name="test_boundary_error")]):
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

    async def call_handler() -> ToolHandlerResult:
        with set_current_run_context(run_context):
            return await handler({})

    with nh.run(StubExecutor()), pytest.raises(ToolBoundaryError, match="tool crashed"):
        anyio.run(call_handler)


def test_backend_handler_invalid_args_returns_retry_prompt_text() -> None:
    def test_arg(run_context, *, x: int) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return x

    step_context = _new_step_context()

    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
    )

    with nh.run(StubExecutor()), nh.scope(tools=[Tool(test_arg, name="test_arg")]):
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

    async def call_invalid() -> ToolHandlerResult:
        with set_current_run_context(run_context):
            return await handler({"x": "not an int"})

    tool_handler_result = anyio.run(call_invalid)
    assert tool_handler_result["kind"] == "retry_prompt"
    assert "Fix the errors and try again." in _render_preview_text_for_test(tool_handler_result)


def test_backend_handler_calls_oversight_once_for_accept_decision(
    tool_span_exporter: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    span_exporter, tracer_provider = tool_span_exporter
    inspection_count = 0

    def test_once_tool() -> int:  # type: ignore[no-untyped-def]
        return 3

    def accept_tool_call(_tool_call: nh.runtime.OperationRequest) -> nh.oversight.Accept:
        nonlocal inspection_count
        inspection_count += 1
        return nh.oversight.Accept()

    step_context = _new_step_context()
    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
        tracer=tracer_provider.get_tracer("tool-tests"),
        trace_include_content=True,
        instrumentation_version=1,
    )

    with nh.run(StubExecutor()), nh.scope(tools=[Tool(test_once_tool, name="test_once_tool")]):
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

    async def call_handler() -> ToolHandlerResult:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_operation=accept_tool_call)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    tool_handler_result = anyio.run(call_handler)
    parsed = json.loads(_render_preview_text_for_test(tool_handler_result))
    assert parsed["value"] == 3
    assert parsed["error"] is None
    assert inspection_count == 1

    tool_spans = _get_finished_tool_spans(span_exporter)
    assert len(tool_spans) == 1
    oversight_event = next(event for event in tool_spans[0].events if event.name == "nighthawk.oversight.decision")
    oversight_attributes = dict(oversight_event.attributes or {})
    assert oversight_attributes["nighthawk.oversight.subject"] == "operation"
    assert oversight_attributes["nighthawk.oversight.verdict"] == "accept"
    assert oversight_attributes["tool.name"] == "test_once_tool"
    assert oversight_attributes["run.id"]
    assert oversight_attributes["scope.id"]
    assert oversight_attributes["step.execution.id"] == "test_tool_boundary"

    tool_span_attributes = dict(tool_spans[0].attributes or {})
    trace_payload_candidates: list[dict[str, object]] = []
    for attribute_value in tool_span_attributes.values():
        if not isinstance(attribute_value, str):
            continue
        try:
            parsed_attribute_value = json.loads(attribute_value)
        except Exception:
            continue
        if not isinstance(parsed_attribute_value, dict):
            continue
        if isinstance(parsed_attribute_value, dict) and "has_error" in parsed_attribute_value and "preview_chars" in parsed_attribute_value:
            trace_payload_candidates.append(parsed_attribute_value)

    assert trace_payload_candidates
    assert trace_payload_candidates[0]["has_error"] is False
    assert isinstance(trace_payload_candidates[0]["preview_chars"], int)
    assert trace_payload_candidates[0]["preview_chars"] > 0
    assert "large" not in json.dumps(trace_payload_candidates[0])


def test_backend_handler_raises_nighthawk_error_for_invalid_tool_oversight_decision() -> None:
    def test_invalid_tool_decision() -> int:  # type: ignore[no-untyped-def]
        return 5

    def invalid_tool_decision(_tool_call: nh.runtime.OperationRequest) -> nh.oversight.Accept | nh.oversight.AbortStep:
        return cast(Any, "bad")

    step_context = _new_step_context()
    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
    )

    with nh.run(StubExecutor()), nh.scope(tools=[Tool(test_invalid_tool_decision, name="test_invalid_tool_decision")]):
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

    async def call_handler() -> ToolHandlerResult:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_operation=invalid_tool_decision)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    with pytest.raises(NighthawkError, match="must return Accept, RejectOperation, or AbortStep"):
        anyio.run(call_handler)


def test_backend_handler_preserves_oversight_abort_and_records_tool_span_event(
    tool_span_exporter: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    span_exporter, tracer_provider = tool_span_exporter
    tool_call_count = 0

    def test_governed_tool() -> int:  # type: ignore[no-untyped-def]
        nonlocal tool_call_count
        tool_call_count += 1
        return 7

    def reject_tool_call(tool_call: nh.runtime.OperationRequest) -> nh.oversight.AbortStep:
        assert tool_call.argument_name_to_value == {}
        return nh.oversight.AbortStep("needs oversight")

    step_context = _new_step_context()
    run_context = RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
        tracer=tracer_provider.get_tracer("tool-tests"),
        trace_include_content=True,
        instrumentation_version=1,
    )

    with nh.run(StubExecutor()), nh.scope(tools=[Tool(test_governed_tool, name="test_governed_tool")]):
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

    async def call_handler() -> ToolHandlerResult:
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_operation=reject_tool_call)),
            set_current_run_context(run_context),
        ):
            return await handler({})

    with pytest.raises(nh.oversight.StepAbortedError, match="needs oversight"):
        anyio.run(call_handler)
    assert tool_call_count == 0

    tool_spans = _get_finished_tool_spans(span_exporter)
    assert len(tool_spans) == 1
    oversight_event = next(event for event in tool_spans[0].events if event.name == "nighthawk.oversight.decision")
    oversight_attributes = dict(oversight_event.attributes or {})
    assert oversight_attributes["nighthawk.oversight.subject"] == "operation"
    assert oversight_attributes["nighthawk.oversight.verdict"] == "abort"
    assert oversight_attributes["tool.name"] == "test_governed_tool"
    assert oversight_attributes["nighthawk.oversight.reason"] == "needs oversight"
    assert oversight_attributes["run.id"]
    assert oversight_attributes["scope.id"]
    assert oversight_attributes["step.execution.id"] == "test_tool_boundary"
