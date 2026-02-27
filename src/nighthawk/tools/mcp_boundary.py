from __future__ import annotations

import json
from typing import Any

import tiktoken
from opentelemetry import context as otel_context
from opentelemetry.context import Context as OtelContext

from ..runtime.step_context import ToolResultRenderingPolicy, get_current_step_context
from .contracts import tool_result_failure_json_text

type ToolHandler = Any

_DEFAULT_TOOL_RESULT_RENDERING_POLICY = ToolResultRenderingPolicy(
    tokenizer_encoding_name="o200k_base",
    tool_result_max_tokens=2_000,
    json_renderer_style="strict",
)


def _resolve_tool_result_rendering_policy() -> ToolResultRenderingPolicy:
    try:
        step_context = get_current_step_context()
    except Exception:
        return _DEFAULT_TOOL_RESULT_RENDERING_POLICY

    if step_context.tool_result_rendering_policy is None:
        return _DEFAULT_TOOL_RESULT_RENDERING_POLICY
    return step_context.tool_result_rendering_policy


def _tool_boundary_failure_text(*, message: str, guidance: str) -> str:
    rendering_policy = _resolve_tool_result_rendering_policy()
    encoding = tiktoken.get_encoding(rendering_policy.tokenizer_encoding_name)
    return tool_result_failure_json_text(
        kind="internal",
        message=message,
        guidance=guidance,
        max_tokens=rendering_policy.tool_result_max_tokens,
        encoding=encoding,
        style=rendering_policy.json_renderer_style,
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


async def call_tool_for_claude_code(
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
    "call_tool_for_claude_code",
    "call_tool_for_low_level_mcp_server",
]
