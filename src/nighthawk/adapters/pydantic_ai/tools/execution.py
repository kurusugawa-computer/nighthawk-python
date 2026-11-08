"""Tool execution wrappers: normalization, classification, and toolset wrapping."""

from __future__ import annotations

import ast
from collections.abc import Awaitable, Callable
from typing import Any

import tiktoken
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, ToolFailed
from pydantic_ai.messages import CachePoint, TextContent, is_multi_modal_content
from pydantic_ai.tools import ToolDenied
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from nighthawk.adapters.pydantic_ai._rendering import project_discovery_value, render_tool_value_text
from nighthawk.adapters.pydantic_ai._user_content import is_top_level_sequence_payload
from nighthawk.adapters.pydantic_ai.model_response import record_response_rejection
from nighthawk.adapters.pydantic_ai.presentation import get_rendering_policy
from nighthawk.adapters.pydantic_ai.tools._rendering import render_tool_error_text
from nighthawk.adapters.pydantic_ai.tools.contracts import ErrorKind, ToolError, ToolOutcome, exception_feedback, rejection_feedback
from nighthawk.core.operation_contract import OperationError, OperationRejectedError
from nighthawk.core.operations import OperationExecution, execute_operation_async, failed_operation, new_operation_request
from nighthawk.core.step_context import StepContext


def _is_native_content(item: object) -> bool:
    return is_multi_modal_content(item) or isinstance(item, CachePoint)


def _normalize_tool_success(value: object, *, root_path: str | None = None) -> ToolOutcome:
    """Render the return in Python form within the tool result budget; multimodal items stay native for transports.

    None stays an empty success, as Pydantic AI renders it.
    """
    if value is None:
        return {"payload": None, "error": None}
    policy = get_rendering_policy()
    encoding = tiktoken.get_encoding(policy.tokenizer_encoding_name)

    def render(item: object, max_tokens: int, path: str | None = None) -> str:
        projected = project_discovery_value(item, max_tokens=max_tokens, encoding=encoding, max_depth=policy.value_max_depth)
        if projected is not None:
            return projected.text
        return render_tool_value_text(item, max_tokens=max_tokens, encoding=encoding, max_depth=policy.value_max_depth, path=path)

    if isinstance(value, TextContent):
        value = value.content
    if _is_native_content(value):
        return {"payload": value, "error": None}
    if is_top_level_sequence_payload(value):
        item_list = [item.content if isinstance(item, TextContent) else item for item in value]
        if any(_is_native_content(item) for item in item_list):
            rendered_item_count = sum(1 for item in item_list if not _is_native_content(item))
            item_max_tokens = max(policy.tool_result_max_tokens // max(rendered_item_count, 1), 1)
            return {"payload": [item if _is_native_content(item) else render(item, item_max_tokens) for item in item_list], "error": None}
        if any(isinstance(item, TextContent) for item in value):
            value = item_list if isinstance(value, list) else tuple(item_list)
    return {"payload": render(value, policy.tool_result_max_tokens, root_path), "error": None}


def _reference_path(expression: object) -> str | None:
    """Return the expression when it is a name with attribute and constant-subscript access, so omitted parts can be read from it."""
    if not isinstance(expression, str):
        return None
    try:
        node = ast.parse(expression.strip(), mode="eval").body
    except SyntaxError:
        return None
    current = node
    while not isinstance(current, ast.Name):
        if isinstance(current, ast.Attribute) or (
            isinstance(current, ast.Subscript)
            and isinstance(current.slice, ast.Constant)
            and (current.slice.value is None or any(type(current.slice.value) is candidate for candidate in (str, int, bool)))
        ):
            current = current.value
        else:
            return None
    return ast.unparse(node)


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
    policy = get_rendering_policy()
    text = render_tool_error_text(
        tool_outcome["error"],
        max_tokens=policy.tool_result_max_tokens,
        encoding=tiktoken.get_encoding(policy.tokenizer_encoding_name),
        max_depth=policy.value_max_depth,
    )
    if tool_outcome["error"].get("status") == "rejected":
        return ToolDenied(text)
    return text


async def _run_tool_and_normalize(
    tool_call: Callable[[], Awaitable[object]], step_context: StepContext, *, root_path: str | None = None
) -> ToolOutcome:
    """Exceptions from Pydantic AI toolset plumbing are handled in ``tool_bridge.execute_tool_call``."""

    try:
        value = await tool_call()
    except OperationRejectedError as exception:
        if step_context._operation_state.failure is not None:
            raise step_context._operation_state.failure from step_context._operation_state.failure.__cause__
        record_response_rejection(step_context, exception.rejection)
        return {"payload": None, "error": rejection_feedback(exception.rejection)}
    except (ModelRetry, CallDeferred, ApprovalRequired, ToolFailed):
        raise
    except Exception as exception:
        error = operation_failure_feedback(step_context, exception)
        if error is None:
            raise
        return {"payload": None, "error": error}

    return _normalize_tool_success(value, root_path=root_path)


def operation_failure_feedback(step_context: StepContext, exception: Exception) -> ToolError | None:
    """Project a recoverable operation failure; None means the exception did not fail a recovered operation."""
    if step_context._operation_state.failure is not None:
        raise step_context._operation_state.failure from step_context._operation_state.failure.__cause__
    if isinstance(exception, OperationError):
        outcome = _normalize_tool_failure(kind=exception.kind, message=str(exception), guidance=exception.guidance)
        assert outcome["error"] is not None
        outcome["error"]["phase"] = exception.phase
        outcome["error"]["evaluation_started"] = exception.evaluation_started
        return outcome["error"]
    finished = failed_operation(step_context, exception=exception)
    return None if finished is None else exception_feedback(finished)


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

            return await execute_operation_async(run_context.deps, request, admit=lambda execution: None, execute=execute)

        metadata = tool.tool_def.metadata or {}
        root_path = _reference_path(tool_args.get("expression")) if metadata.get("nighthawk.operation_kind") == "evaluation" else None
        return await _run_tool_and_normalize(tool_call, run_context.deps, root_path=root_path)

    # Pydantic AI calls this method with its keyword parameter ctx.
    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[StepContext],
        tool: ToolsetTool[StepContext],
    ) -> object:
        tool_outcome = await self.call_tool_outcome(name, tool_args, ctx, tool)
        return _build_standard_tool_return_value(tool_outcome=tool_outcome)
