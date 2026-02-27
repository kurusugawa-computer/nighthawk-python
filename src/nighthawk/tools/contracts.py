from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Generic, Literal, TypeVar

import tiktoken
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from ..json_renderer import JsonableValue, RenderStyle, render_json_text, to_jsonable_value

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
DepsType = TypeVar("DepsType")


class ToolResult(BaseModel, Generic[ValueType], extra="forbid"):
    status: Literal["success", "failure"]
    value: ValueType | None
    error: Error | None


def _render_channel_json_text(
    value: object,
    *,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: RenderStyle,
) -> tuple[str, int]:
    return render_json_text(
        value,
        max_tokens=max_tokens,
        encoding=encoding,
        style=style,
    )


def render_tool_result_json_text(
    *,
    value: object | None,
    error: object | None,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: RenderStyle,
) -> str:
    if error is None:
        error_text = "null"
        error_token_count = 0
        value_max_tokens = max_tokens
    else:
        error_max_tokens = int(max_tokens * 0.9)
        error_text, error_token_count = _render_channel_json_text(
            error,
            max_tokens=error_max_tokens,
            encoding=encoding,
            style=style,
        )
        value_max_tokens = max(max_tokens - error_token_count, 0)

    if value is None:
        value_text = "null"
    else:
        value_text, _ = _render_channel_json_text(
            value,
            max_tokens=value_max_tokens,
            encoding=encoding,
            style=style,
        )

    return f'{{"value":{value_text},"error":{error_text}}}'


def tool_result_success_json_text(
    *,
    value: object,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: RenderStyle,
) -> str:
    return render_tool_result_json_text(
        value=value,
        error=None,
        max_tokens=max_tokens,
        encoding=encoding,
        style=style,
    )


def tool_result_failure_json_text(
    *,
    kind: ErrorKind,
    message: str,
    guidance: str | None,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: RenderStyle,
) -> str:
    error_payload: dict[str, Any] = {
        "kind": kind,
        "message": message,
        "guidance": guidance,
    }
    return render_tool_result_json_text(
        value=None,
        error=error_payload,
        max_tokens=max_tokens,
        encoding=encoding,
        style=style,
    )


def classify_unexpected_exception(exception: BaseException) -> ErrorKind:
    if isinstance(exception, TimeoutError):
        return "transient"
    return "internal"


def normalize_tool_success(value: object) -> ToolResult[JsonableValue]:
    return ToolResult(status="success", value=to_jsonable_value(value), error=None)


def normalize_tool_failure(*, kind: ErrorKind, message: str, guidance: str | None) -> ToolResult[JsonableValue]:
    return ToolResult(
        status="failure",
        value=None,
        error=Error(
            kind=kind,
            message=message,
            guidance=guidance,
        ),
    )


def render_tool_result_for_debug(
    tool_result: ToolResult[JsonableValue],
    *,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: RenderStyle,
) -> str:
    return render_tool_result_json_text(
        value=tool_result.value,
        error=tool_result.error,
        max_tokens=max_tokens,
        encoding=encoding,
        style=style,
    )


async def run_tool_and_normalize(tool_call: Callable[[], Awaitable[object]]) -> ToolResult[JsonableValue]:
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


class ToolResultWrapperToolset(WrapperToolset[DepsType], Generic[DepsType]):
    def __getattr__(self, name: str) -> object:
        return getattr(self.wrapped, name)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[DepsType],
        tool: ToolsetTool[DepsType],
    ) -> ToolResult[JsonableValue]:
        run_context = ctx

        async def tool_call() -> object:
            return await self.wrapped.call_tool(name, tool_args, run_context, tool)

        return await run_tool_and_normalize(tool_call)
