"""Tool execution wrappers: normalization, classification, and toolset wrapping."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from ..errors import NighthawkError
from ..json_renderer import to_jsonable_value
from ..oversight import (
    Accept,
    OversightRejectedError,
    Reject,
    ToolCall,
    record_oversight_decision,
)
from ..runtime.scoping import (
    get_execution_ref,
    get_oversight,
)
from ..runtime.step_context import StepContext
from .contracts import ErrorKind, ToolBoundaryError, ToolError, ToolResult


def _classify_unexpected_exception(exception: BaseException) -> ErrorKind:
    if isinstance(exception, TimeoutError):
        return "transient"
    return "internal"


def _normalize_tool_success(value: object) -> ToolResult:
    return {"value": to_jsonable_value(value), "error": None}


def _normalize_tool_failure(*, kind: ErrorKind, message: str, guidance: str | None) -> ToolResult:
    error: ToolError = {
        "kind": kind,
        "message": message,
        "guidance": guidance,
    }
    return {
        "value": None,
        "error": error,
    }


async def _run_tool_and_normalize(tool_call: Callable[[], Awaitable[object]]) -> ToolResult:
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


def _inspect_tool_call_if_needed(
    *,
    tool_name: str,
    argument_name_to_value: dict[str, Any],
    run_context: RunContext[StepContext],
) -> None:
    oversight = get_oversight()
    if oversight is None or oversight.inspect_tool_call is None:
        return
    step_context = run_context.deps
    if not isinstance(step_context, StepContext):
        raise NighthawkError("Oversight tool inspection requires StepContext dependencies")
    if not step_context.step_id:
        raise NighthawkError("Oversight tool inspection requires StepContext.step_id")

    execution_ref = replace(
        get_execution_ref(),
        step_id=step_context.step_id,
    )
    tool_call = ToolCall(
        execution_ref=execution_ref,
        tool_name=tool_name,
        argument_name_to_value=argument_name_to_value,
        processed_natural_program=step_context.processed_natural_program,
    )
    decision = oversight.inspect_tool_call(tool_call)

    if isinstance(decision, Reject):
        record_oversight_decision(
            subject="tool_call",
            verdict="reject",
            execution_ref=execution_ref,
            tool_name=tool_name,
            reason=decision.reason,
        )
        raise OversightRejectedError(decision.reason)
    if not isinstance(decision, Accept):
        raise NighthawkError("Oversight inspect_tool_call must return Accept or Reject")

    record_oversight_decision(
        subject="tool_call",
        verdict="accept",
        execution_ref=execution_ref,
        tool_name=tool_name,
        reason=decision.reason,
    )


class ToolResultWrapperToolset(WrapperToolset[StepContext]):
    def __getattr__(self, name: str) -> object:
        return getattr(self.wrapped, name)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[StepContext],
        tool: ToolsetTool[StepContext],
    ) -> ToolResult:
        run_context = ctx

        try:
            _inspect_tool_call_if_needed(
                tool_name=name,
                argument_name_to_value=tool_args,
                run_context=run_context,
            )
        except OversightRejectedError as exception:
            return _normalize_tool_failure(
                kind="oversight",
                message=str(exception) or f"Tool call {name!r} was rejected by oversight.",
                guidance="The host rejected this tool call. Choose a different approach or continue without this tool.",
            )

        async def tool_call() -> object:
            return await self.wrapped.call_tool(name, tool_args, run_context, tool)

        tool_result = await _run_tool_and_normalize(tool_call)
        return tool_result
