from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai._instrumentation import InstrumentationNames


def generate_tool_call_id() -> str:
    return str(uuid.uuid4())


def _resolve_instrumentation_names(*, run_context: RunContext[Any]) -> InstrumentationNames:
    return InstrumentationNames.for_version(run_context.instrumentation_version)


def _resolve_trace_include_content(*, run_context: RunContext[Any]) -> bool:
    return run_context.trace_include_content


def _build_tool_span_attributes(
    *,
    tool_name: str,
    tool_call_id: str | None,
    arguments: dict[str, Any],
    instrumentation_names: InstrumentationNames,
    include_content: bool,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "gen_ai.tool.name": tool_name,
        "logfire.msg": f"running tool: {tool_name}",
    }

    if tool_call_id is not None:
        attributes["gen_ai.tool.call.id"] = tool_call_id

    if include_content:
        attributes[instrumentation_names.tool_arguments_attr] = json.dumps(arguments, default=str)
        attributes["logfire.json_schema"] = json.dumps(
            {
                "type": "object",
                "properties": {
                    instrumentation_names.tool_arguments_attr: {"type": "object"},
                    instrumentation_names.tool_result_attr: {"type": "object"},
                    "gen_ai.tool.name": {},
                    "gen_ai.tool.call.id": {},
                },
            }
        )

    return attributes


@contextmanager
def _start_tool_span(
    *,
    tool_name: str,
    attributes: dict[str, Any],
    instrumentation_names: InstrumentationNames,
    run_context: RunContext[Any],
) -> Any:
    span_name = instrumentation_names.get_tool_span_name(tool_name)
    with run_context.tracer.start_as_current_span(span_name, attributes=attributes) as span:
        yield span


async def run_tool_instrumented(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    call: Callable[[], Awaitable[str]],
    run_context: RunContext[Any],
    tool_call_id: str | None,
) -> str:
    instrumentation_names = _resolve_instrumentation_names(run_context=run_context)
    include_content = _resolve_trace_include_content(run_context=run_context)

    span_attributes = _build_tool_span_attributes(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        arguments=arguments,
        instrumentation_names=instrumentation_names,
        include_content=include_content,
    )

    with _start_tool_span(
        tool_name=tool_name,
        attributes=span_attributes,
        instrumentation_names=instrumentation_names,
        run_context=run_context,
    ) as span:
        result_text = await call()
        if include_content and span.is_recording():
            span.set_attribute(instrumentation_names.tool_result_attr, result_text)
        return result_text


__all__ = [
    "generate_tool_call_id",
    "run_tool_instrumented",
]
