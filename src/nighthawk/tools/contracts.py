from __future__ import annotations

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


class ToolResult(TypedDict):
    value: object | None
    error: ToolError | None


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

    return f'{{"value":{value_text},"error":{error_text}}}'
