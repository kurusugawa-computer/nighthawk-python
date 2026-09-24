from __future__ import annotations

import json
from typing import Literal, NotRequired, TypedDict

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
    phase: NotRequired[str]
    evaluation_started: NotRequired[bool]


class ToolOutcome(TypedDict):
    payload: object | None
    error: ToolError | None


type ToolHandlerResultKind = Literal["tool_result", "retry_prompt", "boundary_failure"]


class ToolResultObservation(TypedDict):
    """Carries the canonical ``ToolOutcome``; backend boundaries derive text previews and trace payloads lazily."""

    kind: Literal["tool_result"]
    tool_outcome: ToolOutcome


class RetryPromptObservation(TypedDict):
    kind: Literal["retry_prompt"]
    retry_text: str


class BoundaryFailureObservation(TypedDict):
    """A failure of Nighthawk's tool-handler invocation boundary, distinct from a recoverable ``tool_result`` error.

    Projection failures after a successful ``ToolOutcome`` stay on the ``tool_result`` path and fall back to the preview.
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

    # Wire format uses "value"/"error" keys; the internal ToolOutcome uses
    # "payload"/"error".  The key difference is intentional: "payload" avoids
    # collision with the JSON key and signals that the internal representation
    # may carry non-JSON-serializable content (e.g. multimodal objects).
    return f'{{"value":{value_text},"error":{error_text}}}'
