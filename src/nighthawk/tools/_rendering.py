from __future__ import annotations

import json

import tiktoken

from ..json_renderer import JsonRendererStyle, render_json_text
from .contracts import ToolHandlerResult


def render_tool_handler_result_preview_text(
    *,
    tool_handler_result: ToolHandlerResult,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: JsonRendererStyle,
) -> str:
    if tool_handler_result["kind"] == "tool_result":
        tool_outcome = tool_handler_result["tool_outcome"]
        if tool_outcome["error"] is not None and tool_outcome["error"].get("status") in ("rejected", "skipped"):
            # Control feedback is not a lossy value preview. Preserve its identity and host-supplied reason.
            return json.dumps({"value": None, "error": tool_outcome["error"]}, ensure_ascii=False)
        return render_tool_result_json_text(
            value=tool_outcome["payload"],
            error=tool_outcome["error"],
            max_tokens=max_tokens,
            encoding=encoding,
            style=style,
        )

    return tool_handler_result["retry_text"]


def build_tool_handler_result_trace_text(
    *,
    tool_handler_result: ToolHandlerResult,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: JsonRendererStyle,
) -> str:
    if tool_handler_result["kind"] == "retry_prompt":
        trace_payload = {
            "kind": "retry_prompt",
            "preview_chars": len(tool_handler_result["retry_text"]),
        }
        return json.dumps(trace_payload, ensure_ascii=False, separators=(",", ":"))

    preview_text = render_tool_handler_result_preview_text(
        tool_handler_result=tool_handler_result,
        max_tokens=max_tokens,
        encoding=encoding,
        style=style,
    )

    tool_outcome = tool_handler_result["tool_outcome"]
    trace_payload = {
        "has_error": tool_outcome["error"] is not None,
        "error_kind": tool_outcome["error"]["kind"] if tool_outcome["error"] is not None else None,
        "preview_chars": len(preview_text),
    }
    return json.dumps(trace_payload, ensure_ascii=False, separators=(",", ":"))


def render_tool_result_json_text(
    *,
    value: object | None,
    error: object | None,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: JsonRendererStyle,
) -> str:
    """The sub-values are valid JSON fragments, so assembling them with an f-string is structurally safe."""
    if error is None:
        error_text = "null"
        error_token_count = 0
        value_max_tokens = max_tokens
    else:
        error_max_tokens = int(max_tokens * 0.9)
        error_text, error_token_count = render_json_text(
            error,
            max_tokens=error_max_tokens,
            encoding=encoding,
            style=style,
        )
        value_max_tokens = max(max_tokens - error_token_count, 0)

    if value is None:
        value_text = "null"
    else:
        value_text, _ = render_json_text(
            value,
            max_tokens=value_max_tokens,
            encoding=encoding,
            style=style,
        )

    return f'{{"value":{value_text},"error":{error_text}}}'
