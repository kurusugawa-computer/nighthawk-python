"""Tool execution wrappers: normalization, classification, and toolset wrapping."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.messages import CachePoint, TextContent, ToolReturnContent, is_multi_modal_content
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_core import PydanticSerializationError

from ..json_renderer import to_jsonable_value
from ..runtime._user_content import is_top_level_sequence_payload
from ..runtime.discovery import DiscoveryResult
from ..runtime.operation_contract import OperationError
from ..runtime.operations import OperationExecution, execute_operation_async, new_operation_request
from ..runtime.step_context import StepContext
from .contracts import ErrorKind, ToolError, ToolOutcome

# Intentional Pydantic AI dependency: ToolReturnContent is a scalar
# normalization helper. Multimodal pass-through is guarded explicitly below by
# Nighthawk's UserContent checks so media objects do not get JSON-serialized.
_TOOL_RETURN_CONTENT_ADAPTER: TypeAdapter[object] = TypeAdapter(ToolReturnContent)


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
    if type(item) is DiscoveryResult:
        return item
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
    except OperationError as exception:
        outcome = _normalize_tool_failure(
            kind=exception.kind,
            message=str(exception),
            guidance=exception.guidance,
        )
        assert outcome["error"] is not None
        outcome["error"]["phase"] = exception.phase
        outcome["error"]["evaluation_started"] = exception.evaluation_started
        return outcome

    return _normalize_tool_success(value)


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
        async def tool_call() -> object:
            if (tool.tool_def.metadata or {}).get("nighthawk.provided"):
                return await self.wrapped.call_tool(name, tool_args, run_context, tool)
            request = new_operation_request(run_context.deps, kind="native_tool", tool_name=name, argument_name_to_value=tool_args)

            async def execute(execution: OperationExecution) -> object:
                return await self.wrapped.call_tool(name, tool_args, run_context, tool)

            return await execute_operation_async(run_context.deps, request, admit=lambda: None, execute=execute)

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
        if type(tool_outcome["payload"]) is DiscoveryResult:
            import tiktoken

            from ..runtime.step_context import resolve_tool_result_rendering_policy

            policy = resolve_tool_result_rendering_policy(ctx.deps.tool_result_rendering_policy)
            payload = tool_outcome["payload"]
            assert isinstance(payload, DiscoveryResult)
            return payload.for_budget(policy.tool_result_max_tokens, tiktoken.get_encoding(policy.tokenizer_encoding_name))
        return _build_standard_tool_return_value(tool_outcome=tool_outcome)
