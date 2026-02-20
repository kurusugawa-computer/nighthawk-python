from __future__ import annotations

import json
from typing import Any

import tiktoken
from opentelemetry import context as otel_context
from opentelemetry.context import Context as OtelContext

from ..runtime.scoping import get_environment
from .contracts import tool_result_failure_json_text

type ToolHandler = Any


def _tool_boundary_failure_text(*, message: str, guidance: str) -> str:
    run_configuration = get_environment().run_configuration
    encoding = tiktoken.get_encoding(run_configuration.tokenizer_encoding)
    return tool_result_failure_json_text(
        kind="internal",
        message=message,
        guidance=guidance,
        max_tokens=run_configuration.context_limits.tool_result_max_tokens,
        encoding=encoding,
        style=run_configuration.json_renderer_style,
    )


def _minimal_tool_boundary_failure_json_text(*, message: str, guidance: str) -> str:
    payload = {
        "value": None,
        "error": {
            "kind": "internal",
            "message": message,
            "guidance": guidance,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def _call_tool_handler_result_text(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_handler: ToolHandler,
    parent_otel_context: OtelContext,
) -> str:
    _ = tool_name

    context_token = otel_context.attach(parent_otel_context)
    try:
        try:
            return await tool_handler(arguments)  # type: ignore[misc]
        except Exception as exception:
            try:
                return _tool_boundary_failure_text(
                    message=str(exception) or "Tool boundary wrapper failed",
                    guidance="The tool boundary wrapper failed. Retry or report this error.",
                )
            except Exception:
                return _minimal_tool_boundary_failure_json_text(
                    message=str(exception) or "Tool boundary wrapper failed",
                    guidance=("The tool boundary wrapper failed and could not access the environment. Retry or report this error."),
                )
    finally:
        otel_context.detach(context_token)


async def call_tool_for_claude_agent_sdk(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_handler: ToolHandler,
    parent_otel_context: OtelContext,
) -> dict[str, object]:
    try:
        result_text = await _call_tool_handler_result_text(
            tool_name=tool_name,
            arguments=arguments,
            tool_handler=tool_handler,
            parent_otel_context=parent_otel_context,
        )
    except Exception as exception:
        result_text = _minimal_tool_boundary_failure_json_text(
            message=str(exception) or "Tool boundary wrapper failed",
            guidance="The tool boundary wrapper failed. Retry or report this error.",
        )

    return {"content": [{"type": "text", "text": result_text}]}


async def call_tool_for_low_level_mcp_server(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_handler: ToolHandler,
    parent_otel_context: OtelContext,
) -> list[object]:
    from mcp import types as mcp_types

    try:
        result_text = await _call_tool_handler_result_text(
            tool_name=tool_name,
            arguments=arguments,
            tool_handler=tool_handler,
            parent_otel_context=parent_otel_context,
        )
    except Exception as exception:
        result_text = _minimal_tool_boundary_failure_json_text(
            message=str(exception) or "Tool boundary wrapper failed",
            guidance="The tool boundary wrapper failed. Retry or report this error.",
        )

    return [mcp_types.TextContent(type="text", text=result_text)]


__all__ = [
    "call_tool_for_claude_agent_sdk",
    "call_tool_for_low_level_mcp_server",
]
