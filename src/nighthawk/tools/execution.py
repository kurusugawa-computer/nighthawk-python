"""Tool execution wrappers: normalization, classification, and toolset wrapping."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.messages import CachePoint, TextContent, ToolReturnContent, is_multi_modal_content
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_core import PydanticSerializationError

from ..errors import NighthawkError
from ..json_renderer import to_jsonable_value
from ..oversight import (
    Accept,
    OversightRejectedError,
    Reject,
    ToolCall,
    record_oversight_decision,
)
from ..runtime._user_content import is_top_level_sequence_payload
from ..runtime.scoping import (
    get_execution_ref,
    get_oversight,
)
from ..runtime.step_context import StepContext
from .contracts import ErrorKind, ToolBoundaryError, ToolError, ToolOutcome

# Intentional Pydantic AI dependency: ToolReturnContent is a scalar
# normalization helper. Multimodal pass-through is guarded explicitly below by
# Nighthawk's UserContent checks so media objects do not get JSON-serialized.
_TOOL_RETURN_CONTENT_ADAPTER: TypeAdapter[object] = TypeAdapter(ToolReturnContent)


def _classify_unexpected_exception(exception: BaseException) -> ErrorKind:
    if isinstance(exception, TimeoutError):
        return "transient"
    return "internal"


def _normalize_tool_return_item(item: object) -> object:
    """Normalize a single tool return value for ToolOutcome payload.

    Multimodal content is passed through for native transport. TextContent is
    collapsed to text, and other UserContent markers (``str`` / ``CachePoint``)
    are preserved as-is. Raw bytes are not meaningful JSON tool-return content,
    so they use Nighthawk's JSON preview sentinel. For other scalar values,
    Pydantic AI's ``ToolReturnContent`` adapter helps with JSON-mode
    normalization; values it cannot serialize fall back to
    :func:`to_jsonable_value`. Unexpected exceptions propagate so that
    programming mistakes are not silently swallowed.
    """
    if is_multi_modal_content(item):
        return item
    if isinstance(item, TextContent):
        return item.content
    if isinstance(item, str | CachePoint):
        return item
    if isinstance(item, bytes | bytearray):
        return to_jsonable_value(item)
    try:
        return _TOOL_RETURN_CONTENT_ADAPTER.dump_python(item, mode="json")
    except (PydanticSerializationError, TypeError):
        return to_jsonable_value(item)


def _normalize_tool_success(value: object) -> ToolOutcome:
    if value is None:
        normalized_payload = None
    elif isinstance(value, str | bytes | bytearray):
        normalized_payload = _normalize_tool_return_item(value)
    elif is_top_level_sequence_payload(value):
        normalized_payload = [_normalize_tool_return_item(item) for item in value]
    else:
        normalized_payload = _normalize_tool_return_item(value)

    return {"payload": normalized_payload, "error": None}


def _normalize_tool_failure(*, kind: ErrorKind, message: str, guidance: str | None) -> ToolOutcome:
    error: ToolError = {
        "kind": kind,
        "message": message,
        "guidance": guidance,
    }
    return {
        "payload": None,
        "error": error,
    }


def _build_standard_tool_return_value(*, tool_outcome: ToolOutcome) -> object:
    if tool_outcome["error"] is None:
        return tool_outcome["payload"]
    return {
        "value": tool_outcome["payload"],
        "error": tool_outcome["error"],
    }


async def _run_tool_and_normalize(tool_call: Callable[[], Awaitable[object]]) -> ToolOutcome:
    """Execute a tool call and normalize the outcome to a ToolOutcome.

    Control-flow exceptions are re-raised unchanged.

    This covers exceptions raised by the tool body itself. Exceptions from
    the Pydantic AI toolset plumbing layer (outside this function) are
    handled separately in ``tool_bridge.execute_tool_call``.
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
    except TimeoutError as exception:
        return _normalize_tool_failure(
            kind="transient",
            message=str(exception) or "Tool execution timed out",
            guidance="The tool execution timed out. Retry or choose a different approach.",
        )
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

    async def call_tool_outcome(
        self,
        name: str,
        tool_args: dict[str, Any],
        run_context: RunContext[StepContext],
        tool: ToolsetTool[StepContext],
    ) -> ToolOutcome:
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

    # DEPENDENCY: Pydantic AI's WrapperToolset calls self.call_tool() rather
    # than self.call_tool_outcome().  Keep the upstream parameter name ``ctx``
    # on this compatibility shim, and use ``run_context`` on Nighthawk-owned
    # methods. Re-evaluate after pydantic-ai drops WrapperToolset or exposes a
    # ToolOutcome-aware hook.
    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[StepContext],
        tool: ToolsetTool[StepContext],
    ) -> object:
        """Pydantic AI compatibility shim -- the authoritative API is ``call_tool_outcome``.

        Called by Pydantic AI's tool manager (``self.toolset.call_tool(...)``).
        Must not be removed while Nighthawk uses Pydantic AI's ``WrapperToolset``.
        """
        tool_outcome = await self.call_tool_outcome(name, tool_args, ctx, tool)
        return _build_standard_tool_return_value(tool_outcome=tool_outcome)
