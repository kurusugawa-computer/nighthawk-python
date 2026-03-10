"""Bridge between Nighthawk tools and provider backends.

Provides tool handler construction and tool execution for backends that
expose Nighthawk tools through Pydantic AI FunctionToolset.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import tiktoken
from pydantic_ai import RunContext

# NOTE: pydantic_ai private API — no public alternative for custom backends
# that need to set RunContext for tool execution. Monitor pydantic_ai releases.
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, UnexpectedModelBehavior, UserError
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.toolsets.function import FunctionToolset

from ..runtime.step_context import DEFAULT_TOOL_RESULT_RENDERING_POLICY, StepContext, ToolResultRenderingPolicy
from ..runtime.tool_calls import generate_tool_call_id, run_tool_instrumented
from ..tools.contracts import (
    ToolBoundaryError,
    ToolResult,
    render_tool_result_json_text,
)
from ..tools.execution import ToolResultWrapperToolset

type ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


def resolve_tool_result_rendering_policy(run_context: RunContext[StepContext]) -> ToolResultRenderingPolicy:
    policy = run_context.deps.tool_result_rendering_policy
    if policy is None:
        return DEFAULT_TOOL_RESULT_RENDERING_POLICY
    return policy


def get_current_run_context_required() -> RunContext[StepContext]:
    from pydantic_ai._run_context import get_current_run_context

    run_context = get_current_run_context()
    if run_context is None:
        raise RuntimeError("Nighthawk tool boundaries require an active RunContext")
    return cast(RunContext[StepContext], run_context)


def resolve_allowed_tool_names(
    *,
    model_request_parameters: ModelRequestParameters,
    configured_allowed_tool_names: tuple[str, ...] | None,
    available_tool_names: tuple[str, ...],
) -> tuple[str, ...]:
    if configured_allowed_tool_names is None:
        return tuple(
            name for name in (tool_definition.name for tool_definition in model_request_parameters.function_tools) if name in available_tool_names
        )

    unknown = [name for name in configured_allowed_tool_names if name not in available_tool_names]
    if unknown:
        unknown_list = ", ".join(repr(name) for name in unknown)
        raise ValueError(f"Configured allowed_tool_names includes unknown tools: {unknown_list}")

    return configured_allowed_tool_names


async def execute_tool_call(
    *,
    tool_name: str,
    tool: Any,
    arguments: dict[str, Any],
    run_context: RunContext[StepContext],
    tool_call_id: str,
    rendering_policy: ToolResultRenderingPolicy,
    encoding: tiktoken.Encoding,
) -> str:
    """Execute a single tool call: validate arguments, call the toolset, and render the result."""
    with set_current_run_context(run_context):
        try:
            validated_arguments = tool.args_validator.validate_python(arguments)
        except Exception as exception:
            errors_method = getattr(exception, "errors", None)
            if callable(errors_method):
                try:
                    error_details = errors_method(include_url=False, include_context=False)
                    if isinstance(error_details, list):
                        return RetryPromptPart(
                            tool_name=tool_name,
                            content=error_details,
                            tool_call_id=tool_call_id,
                        ).model_response()
                except Exception:
                    pass

            return RetryPromptPart(
                tool_name=tool_name,
                content=str(exception) or "Invalid tool arguments",
                tool_call_id=tool_call_id,
            ).model_response()

        try:
            tool_result = await tool.toolset.call_tool(tool_name, validated_arguments, run_context, tool)
        except (ModelRetry, CallDeferred, ApprovalRequired):
            raise
        except ToolBoundaryError as exception:
            return render_tool_result_json_text(
                value=None,
                error={"kind": exception.kind, "message": str(exception), "guidance": exception.guidance},
                max_tokens=rendering_policy.tool_result_max_tokens,
                encoding=encoding,
                style=rendering_policy.json_renderer_style,
            )
        except (UserError, UnexpectedModelBehavior) as exception:
            return render_tool_result_json_text(
                value=None,
                error={"kind": "internal", "message": str(exception), "guidance": "The tool backend failed. Retry or report this error."},
                max_tokens=rendering_policy.tool_result_max_tokens,
                encoding=encoding,
                style=rendering_policy.json_renderer_style,
            )
        except Exception as exception:
            return render_tool_result_json_text(
                value=None,
                error={
                    "kind": "internal",
                    "message": str(exception) or "Tool execution failed",
                    "guidance": "The tool execution raised an unexpected error. Retry or report this error.",
                },
                max_tokens=rendering_policy.tool_result_max_tokens,
                encoding=encoding,
                style=rendering_policy.json_renderer_style,
            )

    if isinstance(tool_result, ToolResult):
        return render_tool_result_json_text(
            value=tool_result.value,
            error=None,
            max_tokens=rendering_policy.tool_result_max_tokens,
            encoding=encoding,
            style=rendering_policy.json_renderer_style,
        )

    return render_tool_result_json_text(
        value=tool_result,
        error=None,
        max_tokens=rendering_policy.tool_result_max_tokens,
        encoding=encoding,
        style=rendering_policy.json_renderer_style,
    )


async def build_tool_name_to_handler(
    *,
    model_request_parameters: ModelRequestParameters,
    visible_tools: list[Any],
) -> dict[str, ToolHandler]:
    run_context = get_current_run_context_required()

    toolset = ToolResultWrapperToolset(FunctionToolset(visible_tools))

    tool_name_to_tool = await toolset.get_tools(run_context)

    function_tool_names = {tool_definition.name for tool_definition in model_request_parameters.function_tools}

    tool_name_to_handler: dict[str, ToolHandler] = {}

    for tool_name, tool in tool_name_to_tool.items():
        if tool_name not in function_tool_names:
            continue

        async def handler(
            arguments: dict[str, Any],
            *,
            tool_name: str = tool_name,
            tool: Any = tool,
        ) -> str:
            tool_call_id = generate_tool_call_id()
            from dataclasses import replace

            tool_run_context = replace(
                run_context,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )

            rendering_policy = resolve_tool_result_rendering_policy(run_context)
            encoding = tiktoken.get_encoding(rendering_policy.tokenizer_encoding_name)

            async def call() -> str:
                return await execute_tool_call(
                    tool_name=tool_name,
                    tool=tool,
                    arguments=arguments,
                    run_context=tool_run_context,
                    tool_call_id=tool_call_id,
                    rendering_policy=rendering_policy,
                    encoding=encoding,
                )

            return await run_tool_instrumented(
                tool_name=tool_name,
                arguments=arguments,
                call=call,
                run_context=tool_run_context,
                tool_call_id=tool_call_id,
            )

        tool_name_to_handler[tool_name] = handler

    return tool_name_to_handler


async def prepare_allowed_tools(
    *,
    model_request_parameters: ModelRequestParameters,
    configured_allowed_tool_names: tuple[str, ...] | None,
    visible_tools: list[Any],
) -> tuple[dict[str, Any], dict[str, ToolHandler], tuple[str, ...]]:
    """Build tool definitions, handlers, and allowed names for a backend request."""
    tool_name_to_handler = await build_tool_name_to_handler(
        model_request_parameters=model_request_parameters,
        visible_tools=visible_tools,
    )
    available_tool_names = tuple(tool_name_to_handler.keys())

    allowed_tool_names = resolve_allowed_tool_names(
        model_request_parameters=model_request_parameters,
        configured_allowed_tool_names=configured_allowed_tool_names,
        available_tool_names=available_tool_names,
    )

    tool_name_to_tool_definition = {
        name: tool_definition for name, tool_definition in model_request_parameters.tool_defs.items() if name in allowed_tool_names
    }
    tool_name_to_handler = {name: tool_name_to_handler[name] for name in allowed_tool_names}

    return tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names
