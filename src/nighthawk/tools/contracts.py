from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

_MAX_DEPTH = 8
_MAX_COLLECTION_ITEMS = 100
_MAX_STRING_CHARS = 10_000


type Jsonable = dict[str, "Jsonable"] | list["Jsonable"] | str | int | float | bool | None


def _placeholder_for_value(value: object) -> str:
    return f"<{type(value).__name__}>"


def _truncate_string(value: str) -> str:
    if len(value) <= _MAX_STRING_CHARS:
        return value
    return value[:_MAX_STRING_CHARS] + "...(truncated)"


def _stable_sort_key(value: Jsonable) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    except Exception:
        return str(value)


def best_effort_jsonable(value: object, *, _depth: int = 0) -> Jsonable:
    """Convert an arbitrary Python value into a JSON-compatible Python value.

    Contract:
    - Never raises.
    - Deterministic and bounded.
    - Unknown values become a placeholder string like "<TypeName>".
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            return _placeholder_for_value(value)
        return value

    if isinstance(value, str):
        return _truncate_string(value)

    if isinstance(value, bytes):
        return _placeholder_for_value(value)

    if _depth >= _MAX_DEPTH:
        return "...<max_depth>"

    if isinstance(value, BaseModel):
        try:
            dumped = value.model_dump(mode="python")
        except Exception:
            return _placeholder_for_value(value)
        return best_effort_jsonable(dumped, _depth=_depth + 1)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            field_name_to_value: dict[str, Jsonable] = {}
            for i, field in enumerate(dataclasses.fields(value)):
                if i >= _MAX_COLLECTION_ITEMS:
                    break
                field_name_to_value[field.name] = best_effort_jsonable(getattr(value, field.name), _depth=_depth + 1)
            return field_name_to_value
        except Exception:
            return _placeholder_for_value(value)

    try:
        if isinstance(value, Mapping):
            key_to_value: dict[str, Jsonable] = {}
            items: list[tuple[str, object]] = []
            for i, (key, item_value) in enumerate(value.items()):
                if i >= _MAX_COLLECTION_ITEMS:
                    break
                items.append((str(key), item_value))

            for key, item_value in sorted(items, key=lambda item: item[0]):
                key_to_value[key] = best_effort_jsonable(item_value, _depth=_depth + 1)

            return key_to_value

        if isinstance(value, (set, frozenset)):
            limited_items = list(value)[:_MAX_COLLECTION_ITEMS]
            converted = [best_effort_jsonable(item, _depth=_depth + 1) for item in limited_items]
            return sorted(converted, key=_stable_sort_key)

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            limited_items = list(value)[:_MAX_COLLECTION_ITEMS]
            return [best_effort_jsonable(item, _depth=_depth + 1) for item in limited_items]

    except Exception:
        return _placeholder_for_value(value)

    return _placeholder_for_value(value)


def serialize_value_to_json_text(
    value: object,
    *,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
) -> str:
    try:
        jsonable = best_effort_jsonable(value)
        return json.dumps(
            jsonable,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
        )
    except Exception:
        return json.dumps(
            _placeholder_for_value(value),
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
            "value": best_effort_jsonable(value),
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


def classify_unexpected_exception(exception: BaseException) -> ErrorKind:
    if isinstance(exception, TimeoutError):
        return "transient"
    return "internal"


def normalize_tool_success(value: object) -> ToolResult[Jsonable]:
    return ToolResult(status="success", value=best_effort_jsonable(value), error=None)


def normalize_tool_failure(*, kind: ErrorKind, message: str, guidance: str | None) -> ToolResult[Jsonable]:
    return ToolResult(
        status="failure",
        value=None,
        error=Error(
            kind=kind,
            message=sanitize_error_message(message),
            guidance=guidance,
        ),
    )


async def run_tool_and_normalize(tool_call: Callable[[], Awaitable[object]]) -> ToolResult[Jsonable]:
    """Execute a tool call and normalize the outcome to a ToolResult.

    Control-flow exceptions are re-raised unchanged.
    """

    try:
        value = await tool_call()
    except (ModelRetry, CallDeferred, ApprovalRequired):
        raise
    except ToolBoundaryFailure as exception:
        return normalize_tool_failure(
            kind=exception.kind,
            message=str(exception),
            guidance=exception.guidance,
        )
    except TimeoutError:
        raise ModelRetry("Tool execution timed out. Retry.") from None
    except Exception as exception:
        kind = classify_unexpected_exception(exception)
        return normalize_tool_failure(
            kind=kind,
            message=str(exception) or "Tool execution failed",
            guidance="The tool execution raised an unexpected error. Retry or report this error.",
        )

    return normalize_tool_success(value)


class ToolResultWrapperToolset(WrapperToolset[Any]):
    def __getattr__(self, name: str) -> object:
        # WrapperToolset intentionally does not proxy attributes.
        # Nighthawk tests and some callers inspect FunctionToolset internals (e.g. `.tools`).
        return getattr(self.wrapped, name)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[Any],
        tool: ToolsetTool[Any],
    ) -> ToolResult[Jsonable]:
        async def tool_call() -> object:
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)

        return await run_tool_and_normalize(tool_call)
