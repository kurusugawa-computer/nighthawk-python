"""Tool execution wrappers: normalization, classification, and toolset wrapping."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from ..json_renderer import JsonableValue, to_jsonable_value
from .contracts import ErrorKind, ToolBoundaryError, ToolResult, _Error


def _classify_unexpected_exception(exception: BaseException) -> ErrorKind:
    if isinstance(exception, TimeoutError):
        return "transient"
    return "internal"


def _normalize_tool_success(value: object) -> ToolResult[JsonableValue]:
    return ToolResult(value=to_jsonable_value(value), error=None)


def _normalize_tool_failure(*, kind: ErrorKind, message: str, guidance: str | None) -> ToolResult[JsonableValue]:
    return ToolResult(
        value=None,
        error=_Error(
            kind=kind,
            message=message,
            guidance=guidance,
        ),
    )


async def _run_tool_and_normalize(tool_call: Callable[[], Awaitable[object]]) -> ToolResult[JsonableValue]:
    """Execute a tool call and normalize the outcome to a ToolResult.

    Control-flow exceptions are re-raised unchanged.
    """

    try:
        value = await tool_call()
    except (ModelRetry, CallDeferred, ApprovalRequired):
        raise
    except ToolBoundaryError as exception:
        return _normalize_tool_failure(
            kind=exception.kind,
            message=str(exception),
            guidance=exception.guidance,
        )
    except TimeoutError:
        raise ModelRetry("Tool execution timed out. Retry.") from None
    except Exception as exception:
        kind = _classify_unexpected_exception(exception)
        return _normalize_tool_failure(
            kind=kind,
            message=str(exception) or "Tool execution failed",
            guidance="The tool execution raised an unexpected error. Retry or report this error.",
        )

    return _normalize_tool_success(value)


class ToolResultWrapperToolset[DepsType](WrapperToolset[DepsType]):
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

        return await _run_tool_and_normalize(tool_call)
