from __future__ import annotations

import json

import tiktoken
from opentelemetry import context as otel_context
from opentelemetry.context import Context as OtelContext

from ..runtime.step_context import DEFAULT_TOOL_RESULT_RENDERING_POLICY, ToolResultRenderingPolicy, get_current_step_context
from ..tools.contracts import render_tool_result_json_text
from .tool_bridge import ToolHandler


def _resolve_tool_result_rendering_policy() -> ToolResultRenderingPolicy:
    try:
        step_context = get_current_step_context()
    except Exception:
        return DEFAULT_TOOL_RESULT_RENDERING_POLICY

    if step_context.tool_result_rendering_policy is None:
        return DEFAULT_TOOL_RESULT_RENDERING_POLICY
    return step_context.tool_result_rendering_policy


def _tool_boundary_failure_text(*, message: str, guidance: str) -> str:
    rendering_policy = _resolve_tool_result_rendering_policy()
    encoding = tiktoken.get_encoding(rendering_policy.tokenizer_encoding_name)
    return render_tool_result_json_text(
        value=None,
        error={"kind": "internal", "message": message, "guidance": guidance},
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
        return await tool_handler(arguments)
    except Exception as exception:
        error_message = str(exception) or "Tool boundary wrapper failed"
        try:
            return _tool_boundary_failure_text(
                message=error_message,
                guidance="The tool boundary wrapper failed. Retry or report this error.",
            )
        except Exception:
            return _minimal_tool_boundary_failure_json_text(
                message=error_message,
                guidance="The tool boundary wrapper failed and could not access the environment. Retry or report this error.",
            )
    finally:
        otel_context.detach(context_token)


async def _get_safe_tool_result_text(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_handler: ToolHandler,
    parent_otel_context: OtelContext,
) -> str:
    try:
        return await _call_tool_handler_result_text(
            tool_name=tool_name,
            arguments=arguments,
            tool_handler=tool_handler,
            parent_otel_context=parent_otel_context,
        )
    except Exception as exception:
        return _minimal_tool_boundary_failure_json_text(
            message=str(exception) or "Tool boundary wrapper failed",
            guidance="The tool boundary wrapper failed. Retry or report this error.",
        )


async def call_tool_for_claude_code_sdk(
    *,
    tool_name: str,
    arguments: dict[str, object],
    tool_handler: ToolHandler,
    parent_otel_context: OtelContext,
) -> dict[str, object]:
    result_text = await _get_safe_tool_result_text(
        tool_name=tool_name,
        arguments=arguments,
        tool_handler=tool_handler,
        parent_otel_context=parent_otel_context,
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

    result_text = await _get_safe_tool_result_text(
        tool_name=tool_name,
        arguments=arguments,
        tool_handler=tool_handler,
        parent_otel_context=parent_otel_context,
    )
    return [mcp_types.TextContent(type="text", text=result_text)]
