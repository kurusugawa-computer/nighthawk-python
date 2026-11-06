"""Tool execution wrappers: normalization, classification, and toolset wrapping."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import tiktoken
from pydantic import TypeAdapter
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry
from pydantic_ai.messages import CachePoint, TextContent, ToolReturnContent, is_multi_modal_content
from pydantic_ai.tools import ToolDenied
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_core import PydanticSerializationError

from nighthawk.adapters.pydantic_ai._rendering import project_discovery_value, to_jsonable_value
from nighthawk.adapters.pydantic_ai._user_content import is_top_level_sequence_payload
from nighthawk.adapters.pydantic_ai.model_response import record_response_rejection
from nighthawk.adapters.pydantic_ai.presentation import get_rendering_policy
from nighthawk.adapters.pydantic_ai.tools.contracts import ErrorKind, ToolError, ToolOutcome, rejection_feedback
from nighthawk.core.discovery import DiscoveryResult, contains_discovery_result, map_discovery_containers
from nighthawk.core.operation_contract import OperationError, OperationRejectedError
from nighthawk.core.operations import OperationExecution, execute_operation_async, new_operation_request
from nighthawk.core.step_context import StepContext

# Multimodal pass-through is guarded explicitly below by Nighthawk's UserContent checks so media objects do not get JSON-serialized.
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
    if tool_outcome["error"].get("status") == "rejected":
        return ToolDenied(json.dumps(tool_outcome["error"], ensure_ascii=False))
    return {
        "value": tool_outcome["payload"],
        "error": tool_outcome["error"],
    }


async def _run_tool_and_normalize(tool_call: Callable[[], Awaitable[object]], step_context: StepContext) -> ToolOutcome:
    """Exceptions from Pydantic AI toolset plumbing are handled in ``tool_bridge.execute_tool_call``."""

    try:
        value = await tool_call()
    except OperationRejectedError as exception:
        if step_context._operation_state.failure is not None:
            raise step_context._operation_state.failure from step_context._operation_state.failure.__cause__
        record_response_rejection(step_context, exception.rejection)
        return {"payload": None, "error": rejection_feedback(exception.rejection)}
    except (ModelRetry, CallDeferred, ApprovalRequired):
        raise
    except OperationError as exception:
        if step_context._operation_state.failure is not None:
            raise step_context._operation_state.failure from step_context._operation_state.failure.__cause__
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


RECOVERABLE_OPERATION_ERRORS = (ModelRetry, ApprovalRequired, CallDeferred)


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
        run_context.deps.recoverable_operation_errors = RECOVERABLE_OPERATION_ERRORS

        async def tool_call() -> object:
            if (tool.tool_def.metadata or {}).get("nighthawk.provided"):
                return await self.wrapped.call_tool(name, tool_args, run_context, tool)
            request = new_operation_request(run_context.deps, kind="native_tool", tool_name=name, argument_name_to_value=tool_args)

            async def execute(execution: OperationExecution) -> object:
                return await self.wrapped.call_tool(name, tool_args, run_context, tool)

            return await execute_operation_async(run_context.deps, request, admit=lambda execution: None, execute=execute)

        return await _run_tool_and_normalize(tool_call, run_context.deps)

    # Pydantic AI calls this method with its keyword parameter ctx.
    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[StepContext],
        tool: ToolsetTool[StepContext],
    ) -> object:
        tool_outcome = await self.call_tool_outcome(name, tool_args, ctx, tool)
        if contains_discovery_result(tool_outcome["payload"]):
            policy = get_rendering_policy()
            projected = project_discovery_value(
                tool_outcome["payload"], max_tokens=policy.tool_result_max_tokens, encoding=tiktoken.get_encoding(policy.tokenizer_encoding_name)
            )
            assert projected is not None
            return projected.value
        return _build_standard_tool_return_value(tool_outcome=tool_outcome)
