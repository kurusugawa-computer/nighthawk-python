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

from ..json_renderer import project_discovery_value, to_jsonable_value
from ..runtime._user_content import is_top_level_sequence_payload
from ..runtime.discovery import DiscoveryResult, contains_discovery_result, map_discovery_containers
from ..runtime.operation_contract import OperationError
from ..runtime.operations import OperationExecution, execute_operation_async, new_operation_request
from ..runtime.step_context import StepContext
from .contracts import ErrorKind, ToolError, ToolOutcome

# Intentional Pydantic AI dependency: ToolReturnContent is a scalar
# normalization helper. Multimodal pass-through is guarded explicitly below by
# Nighthawk's UserContent checks so media objects do not get JSON-serialized.
_TOOL_RETURN_CONTENT_ADAPTER: TypeAdapter[object] = TypeAdapter(ToolReturnContent)


def _normalize_tool_return_item(item: object) -> object:
    """Multimodal items pass through for native transport; raw bytes are not meaningful JSON and use the preview sentinel."""
    if type(item) is DiscoveryResult:
        return item
    if contains_discovery_result(item):
        return map_discovery_containers(item, _normalize_tool_return_item)
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
    if contains_discovery_result(value):
        normalized_payload = map_discovery_containers(value, _normalize_tool_return_item)
    elif value is None:
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
    """Exceptions from Pydantic AI toolset plumbing are handled in ``tool_bridge.execute_tool_call``."""

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
        if contains_discovery_result(tool_outcome["payload"]):
            import tiktoken

            from ..runtime.step_context import resolve_tool_result_rendering_policy

            policy = resolve_tool_result_rendering_policy(ctx.deps.tool_result_rendering_policy)
            projected = project_discovery_value(
                tool_outcome["payload"], max_tokens=policy.tool_result_max_tokens, encoding=tiktoken.get_encoding(policy.tokenizer_encoding_name)
            )
            assert projected is not None
            return projected.value
        return _build_standard_tool_return_value(tool_outcome=tool_outcome)
