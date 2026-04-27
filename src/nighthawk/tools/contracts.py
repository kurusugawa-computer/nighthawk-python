from __future__ import annotations

import json
from typing import Literal, TypedDict

import tiktoken

from ..json_renderer import JsonRendererStyle, render_json_text

type ErrorKind = Literal["invalid_input", "resolution", "execution", "transient", "internal", "oversight"]


class ToolBoundaryError(Exception):
    def __init__(self, *, kind: ErrorKind, message: str, guidance: str | None = None) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.guidance: str | None = guidance


class ToolError(TypedDict):
    kind: ErrorKind
    message: str
    guidance: str | None


class ToolOutcome(TypedDict):
    payload: object | None
    error: ToolError | None


type ToolHandlerResultKind = Literal["tool_result", "retry_prompt", "boundary_failure"]


class ToolResultObservation(TypedDict):
    """Successful or recoverable tool execution wrapped for backend transport.

    Carries the canonical ``ToolOutcome`` so multimodal-capable transports can
    emit native content blocks. Text previews and trace payloads are derived
    lazily by backend boundaries that need them.
    """

    kind: Literal["tool_result"]
    tool_outcome: ToolOutcome


class RetryPromptObservation(TypedDict):
    """Retry prompt produced when tool argument validation failed."""

    kind: Literal["retry_prompt"]
    retry_text: str


class BoundaryFailureObservation(TypedDict):
    """Failure raised by the tool-handler invocation boundary itself.

    This observation is reserved for failures in the wrapper/boundary path that
    calls the tool handler. It is intentionally separate from ``tool_result``
    failures: ``boundary_failure`` means Nighthawk's invocation boundary failed,
    while ``tool_result`` with an error means the selected tool body or toolset
    plumbing produced a recoverable outcome for the model. Rich projection
    failures that happen later while converting a successful ``ToolOutcome`` to
    backend-specific content stay on the normal ``tool_result`` path and fall
    back to preview rendering there.
    """

    kind: Literal["boundary_failure"]
    tool_error: ToolError


type ToolHandlerResult = ToolResultObservation | RetryPromptObservation | BoundaryFailureObservation


def build_tool_result_observation(
    *,
    tool_outcome: ToolOutcome,
) -> ToolResultObservation:
    return {
        "kind": "tool_result",
        "tool_outcome": tool_outcome,
    }


def render_tool_handler_result_preview_text(
    *,
    tool_handler_result: ToolHandlerResult,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: JsonRendererStyle,
) -> str:
    if tool_handler_result["kind"] == "tool_result":
        tool_outcome = tool_handler_result["tool_outcome"]
        return render_tool_result_json_text(
            value=tool_outcome["payload"],
            error=tool_outcome["error"],
            max_tokens=max_tokens,
            encoding=encoding,
            style=style,
        )

    if tool_handler_result["kind"] == "retry_prompt":
        return tool_handler_result["retry_text"]

    tool_error = tool_handler_result["tool_error"]
    return render_tool_result_json_text(
        value=None,
        error=tool_error,
        max_tokens=max_tokens,
        encoding=encoding,
        style=style,
    )


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

    if tool_handler_result["kind"] == "boundary_failure":
        trace_payload = {
            "kind": "boundary_failure",
            "preview_chars": len(preview_text),
        }
        return json.dumps(trace_payload, ensure_ascii=False, separators=(",", ":"))

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
    """Render a tool result envelope as compact JSON text.

    Both ``value`` and ``error`` are individually previewed under their own
    token budgets via ``render_json_text``, then assembled into a
    ``{"value": ..., "error": ...}`` envelope using f-string interpolation.
    The sub-values are already valid JSON fragments produced by
    ``render_json_text`` / ``json.dumps``, so the f-string concatenation
    is structurally safe.
    """
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

    # Wire format uses "value"/"error" keys; the internal ToolOutcome uses
    # "payload"/"error".  The key difference is intentional: "payload" avoids
    # collision with the JSON key and signals that the internal representation
    # may carry non-JSON-serializable content (e.g. multimodal objects).
    return f'{{"value":{value_text},"error":{error_text}}}'
