"""Bridge between Nighthawk tools and provider backends.

Provides tool handler construction and tool execution for backends that
expose Nighthawk tools through Pydantic AI FunctionToolset.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import tiktoken
from pydantic_ai import RunContext

# NOTE: pydantic_ai private API -- no public alternative for custom backends
# that need to bridge RunContext across non-Pydantic-AI transports. Monitor
# pydantic_ai releases and keep private access centralized in this module.
from pydantic_ai._run_context import set_current_run_context
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry, UnexpectedModelBehavior, UserError
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.toolsets.function import FunctionToolset

from ..errors import NighthawkError
from ..runtime.step_context import (
    DEFAULT_TOOL_RESULT_RENDERING_POLICY,
    StepContext,
    ToolResultRenderingPolicy,
    resolve_tool_result_rendering_policy,
)
from ..runtime.tool_calls import generate_tool_call_id, run_tool_instrumented
from ..tools.contracts import (
    RetryPromptObservation,
    ToolError,
    ToolHandlerResult,
    ToolOutcome,
    build_tool_handler_result_trace_text,
    build_tool_result_observation,
)
from ..tools.execution import ToolResultWrapperToolset

type ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolHandlerResult]]


def _build_retry_prompt_observation(*, retry_text: str) -> RetryPromptObservation:
    return {
        "kind": "retry_prompt",
        "retry_text": retry_text,
    }


def build_tool_handler_result_trace_payload_text(
    *,
    tool_handler_result: ToolHandlerResult,
    rendering_policy: ToolResultRenderingPolicy,
    encoding: tiktoken.Encoding,
) -> str:
    return build_tool_handler_result_trace_text(
        tool_handler_result=tool_handler_result,
        max_tokens=rendering_policy.tool_result_max_tokens,
        encoding=encoding,
        style=rendering_policy.json_renderer_style,
    )


def _get_current_pydantic_ai_run_context() -> object | None:
    from pydantic_ai._run_context import get_current_run_context

    return get_current_run_context()


def get_current_step_run_context_optional() -> RunContext[StepContext] | None:
    run_context = _get_current_pydantic_ai_run_context()
    deps = getattr(run_context, "deps", None)
    if run_context is None or not isinstance(deps, StepContext):
        return None
    return cast(RunContext[StepContext], run_context)


def get_current_step_run_context_required(*, boundary_name: str) -> RunContext[StepContext]:
    run_context = _get_current_pydantic_ai_run_context()
    if run_context is None:
        raise NighthawkError(f"{boundary_name} requires an active RunContext")
    deps = getattr(run_context, "deps", None)
    if not isinstance(deps, StepContext):
        raise UnexpectedModelBehavior(f"{boundary_name} requires StepContext dependencies")
    return cast(RunContext[StepContext], run_context)


def resolve_current_tool_result_rendering_policy() -> ToolResultRenderingPolicy:
    run_context = get_current_step_run_context_optional()
    if run_context is None:
        return DEFAULT_TOOL_RESULT_RENDERING_POLICY
    return resolve_tool_result_rendering_policy(run_context.deps.tool_result_rendering_policy)


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
    toolset: ToolResultWrapperToolset,
    arguments: dict[str, Any],
    run_context: RunContext[StepContext],
    tool_call_id: str,
) -> ToolHandlerResult:
    """Execute a single tool call and build model/trace observations."""
    with set_current_run_context(run_context):
        try:
            validated_arguments = tool.args_validator.validate_python(arguments)
        except Exception as exception:
            errors_method = getattr(exception, "errors", None)
            if callable(errors_method):
                try:
                    error_details = errors_method(include_url=False, include_context=False)
                    if isinstance(error_details, list):
                        retry_text = RetryPromptPart(
                            tool_name=tool_name,
                            content=error_details,
                            tool_call_id=tool_call_id,
                        ).model_response()
                        return _build_retry_prompt_observation(retry_text=retry_text)
                except Exception:
                    pass

            retry_text = RetryPromptPart(
                tool_name=tool_name,
                content=str(exception) or "Invalid tool arguments",
                tool_call_id=tool_call_id,
            ).model_response()
            return _build_retry_prompt_observation(retry_text=retry_text)

        try:
            tool_outcome = await toolset.call_tool_outcome(tool_name, validated_arguments, run_context, tool)
        except (ModelRetry, CallDeferred, ApprovalRequired):
            raise
        except NighthawkError:
            raise
        # Errors below originate from the Pydantic AI toolset plumbing layer
        # (outside ToolResultWrapperToolset._run_tool_and_normalize, which
        # normalizes tool-body exceptions into ToolOutcome error payloads).
        # These catch blocks convert plumbing failures into ToolOutcome so the
        # model receives an actionable error observation rather than a crash.
        except (UserError, UnexpectedModelBehavior) as exception:
            tool_error: ToolError = {
                "kind": "internal",
                "message": str(exception),
                "guidance": "The tool backend failed. Retry or report this error.",
            }
            tool_outcome = cast(ToolOutcome, {"payload": None, "error": tool_error})
        except Exception as exception:
            tool_error = {
                "kind": "internal",
                "message": str(exception) or "Tool execution failed",
                "guidance": "The tool execution raised an unexpected error. Retry or report this error.",
            }
            tool_outcome = cast(ToolOutcome, {"payload": None, "error": tool_error})

    return build_tool_result_observation(tool_outcome=tool_outcome)


async def build_tool_name_to_handler(
    *,
    model_request_parameters: ModelRequestParameters,
    visible_tools: list[Any],
) -> dict[str, ToolHandler]:
    run_context = get_current_step_run_context_required(boundary_name="Nighthawk function tool boundary")

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
        ) -> ToolHandlerResult:
            tool_call_id = generate_tool_call_id()
            from dataclasses import replace

            tool_run_context = replace(
                run_context,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )

            rendering_policy = resolve_tool_result_rendering_policy(run_context.deps.tool_result_rendering_policy)
            encoding = tiktoken.get_encoding(rendering_policy.tokenizer_encoding_name)

            async def call() -> ToolHandlerResult:
                return await execute_tool_call(
                    tool_name=tool_name,
                    tool=tool,
                    toolset=toolset,
                    arguments=arguments,
                    run_context=tool_run_context,
                    tool_call_id=tool_call_id,
                )

            def build_trace_text(tool_handler_result: ToolHandlerResult) -> str | None:
                if not tool_run_context.trace_include_content:
                    return None
                return build_tool_handler_result_trace_payload_text(
                    tool_handler_result=tool_handler_result,
                    rendering_policy=rendering_policy,
                    encoding=encoding,
                )

            return await run_tool_instrumented(
                tool_name=tool_name,
                arguments=arguments,
                call=call,
                build_trace_text=build_trace_text,
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
