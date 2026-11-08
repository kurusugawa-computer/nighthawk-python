from __future__ import annotations

import json

import tiktoken

from nighthawk.adapters.pydantic_ai._rendering import render_complete_value_text, render_tool_value_text, render_value_within_budget
from nighthawk.adapters.pydantic_ai.tools.contracts import ToolError, ToolHandlerResult


def render_tool_error_text(error: ToolError, *, max_tokens: int, encoding: tiktoken.Encoding, max_depth: int) -> str:
    if error.get("status") in ("rejected", "skipped"):
        # Control feedback is not a lossy value preview. Preserve its identity and host-supplied reason.
        return "error = " + render_complete_value_text(error, max_depth=max_depth)
    return "error = " + render_value_within_budget(error, max_tokens=max_tokens, encoding=encoding, max_depth=max_depth)[0].text


def render_tool_handler_result_preview_text(
    *,
    tool_handler_result: ToolHandlerResult,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    max_depth: int,
) -> str:
    if tool_handler_result["kind"] == "tool_result":
        tool_outcome = tool_handler_result["tool_outcome"]
        if tool_outcome["error"] is not None:
            return render_tool_error_text(tool_outcome["error"], max_tokens=max_tokens, encoding=encoding, max_depth=max_depth)
        payload = tool_outcome["payload"]
        if type(payload) is str:
            return payload
        return render_tool_value_text(payload, max_tokens=max_tokens, encoding=encoding, max_depth=max_depth)

    return tool_handler_result["retry_text"]


def build_tool_handler_result_trace_text(
    *,
    tool_handler_result: ToolHandlerResult,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    max_depth: int,
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
        max_depth=max_depth,
    )

    tool_outcome = tool_handler_result["tool_outcome"]
    trace_payload = {
        "has_error": tool_outcome["error"] is not None,
        "error_kind": tool_outcome["error"]["kind"] if tool_outcome["error"] is not None else None,
        "preview_chars": len(preview_text),
    }
    return json.dumps(trace_payload, ensure_ascii=False, separators=(",", ":"))
