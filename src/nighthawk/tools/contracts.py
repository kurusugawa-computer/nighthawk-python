from __future__ import annotations

import json
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel
from pydantic_core import to_jsonable_python


def serialize_value_to_json_text(
    value: object,
    *,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
) -> str:
    try:
        jsonable = to_jsonable_python(
            value,
            serialize_unknown=True,
            fallback=lambda v: f"<{type(v).__name__}>",
        )
        return json.dumps(
            jsonable,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )
    except Exception:
        return json.dumps(
            f"<{type(value).__name__}>",
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )


type ErrorKind = Literal["invalid_input", "resolution", "execution", "transient", "internal"]


class ToolBoundaryFailure(Exception):
    def __init__(self, *, kind: ErrorKind, message: str, guidance: str | None = None) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.guidance: str | None = guidance


class Error(BaseModel, extra="forbid"):
    kind: ErrorKind
    message: str
    guidance: str | None = None


ValueType = TypeVar("ValueType")


class ToolResult(BaseModel, Generic[ValueType], extra="forbid"):
    status: Literal["success", "failure"]
    value: ValueType | None
    error: Error | None


_MAX_ERROR_MESSAGE_CHARS = 2000


def _truncate_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    snipped = len(text) - max_chars
    return f"{text[:max_chars]}...(snipped {snipped} chars)"


def sanitize_error_message(message: str) -> str:
    # Conservative: do not include tracebacks or large dumps.
    sanitized = " ".join(message.split())
    return _truncate_text(sanitized, max_chars=_MAX_ERROR_MESSAGE_CHARS)


def tool_result_success_json_text(value: object) -> str:
    try:
        payload: dict[str, Any] = {
            "status": "success",
            "value": value,
            "error": None,
        }
        return serialize_value_to_json_text(payload)
    except Exception as exception:
        # serialize_value_to_json_text should already be never-raise, but keep a final guard.
        message = sanitize_error_message(str(exception) or "Failed to serialize tool success result")
        return tool_result_failure_json_text(kind="internal", message=message, guidance=None)


def tool_result_failure_json_text(
    *,
    kind: ErrorKind,
    message: str,
    guidance: str | None,
) -> str:
    try:
        payload: dict[str, Any] = {
            "status": "failure",
            "value": None,
            "error": {
                "kind": kind,
                "message": sanitize_error_message(message),
                "guidance": guidance,
            },
        }
        return serialize_value_to_json_text(payload)
    except Exception as exception:
        # If this fails, we must still return valid JSON text.
        fallback = {
            "status": "failure",
            "value": None,
            "error": {
                "kind": "internal",
                "message": sanitize_error_message(str(exception) or "Failed to serialize tool failure result"),
                "guidance": None,
            },
        }
        return serialize_value_to_json_text(fallback)
