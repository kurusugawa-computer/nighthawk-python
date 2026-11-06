from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import tiktoken
from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai._instrumentation import InstrumentationNames

# Pydantic AI exposes its active transport context only through this private module.
from pydantic_ai._run_context import get_current_run_context, set_current_run_context
from pydantic_ai.exceptions import ModelRetry, ToolFailed, UnexpectedModelBehavior
from pydantic_ai.messages import RetryInfo, ToolCallPart, ToolReturnPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import ToolDenied
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.function import FunctionToolset

from nighthawk._identifiers import generate_ulid
from nighthawk.adapters.pydantic_ai.models.tool_transport import wrap_transported_tool
from nighthawk.adapters.pydantic_ai.presentation import (
    get_rendering_policy,
)
from nighthawk.adapters.pydantic_ai.tools._admission import with_tool_admission
from nighthawk.adapters.pydantic_ai.tools._rendering import build_tool_handler_result_trace_text
from nighthawk.adapters.pydantic_ai.tools.contracts import (
    RetryPromptObservation,
    ToolError,
    ToolHandlerResult,
    ToolOutcome,
    ToolResultObservation,
    exception_feedback,
)
from nighthawk.adapters.pydantic_ai.tools.execution import ToolResultWrapperToolset, _build_standard_tool_return_value, _normalize_tool_success
from nighthawk.core.operations import failed_operation
from nighthawk.core.step_context import StepContext
from nighthawk.errors import NighthawkError

type ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolHandlerResult]]


def _build_tool_span_attributes(
    *,
    tool_name: str,
    tool_call_id: str,
    argument_name_to_value: dict[str, Any],
    instrumentation_names: InstrumentationNames,
    include_content: bool,
) -> dict[str, Any]:
    attribute_name_to_value: dict[str, Any] = {
        "gen_ai.tool.name": tool_name,
        "logfire.msg": f"running tool: {tool_name}",
    }

    attribute_name_to_value["gen_ai.tool.call.id"] = tool_call_id

    if include_content:
        attribute_name_to_value[instrumentation_names.tool_arguments_attr] = json.dumps(argument_name_to_value, default=str)
        attribute_name_to_value["logfire.json_schema"] = json.dumps(
            {
                "type": "object",
                "properties": {
                    instrumentation_names.tool_arguments_attr: {"type": "object"},
                    instrumentation_names.tool_result_attr: {"type": "object"},
                    "gen_ai.tool.name": {},
                    "gen_ai.tool.call.id": {},
                },
            }
        )

    return attribute_name_to_value


async def _run_tool_instrumented(
    *,
    tool_name: str,
    argument_name_to_value: dict[str, Any],
    call: Callable[[], Awaitable[ToolHandlerResult]],
    build_trace_text: Callable[[ToolHandlerResult], str],
    run_context: RunContext[Any],
    tool_call_id: str,
) -> ToolHandlerResult:
    instrumentation_names = InstrumentationNames.for_version(run_context.instrumentation_version)
    include_content = run_context.trace_include_content

    attribute_name_to_value = _build_tool_span_attributes(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        argument_name_to_value=argument_name_to_value,
        instrumentation_names=instrumentation_names,
        include_content=include_content,
    )

    with run_context.tracer.start_as_current_span(instrumentation_names.get_tool_span_name(tool_name), attributes=attribute_name_to_value) as span:
        result = await call()
        if include_content and span.is_recording():
            span.set_attribute(instrumentation_names.tool_result_attr, build_trace_text(result))
        return result


class _ObservedToolset(ToolResultWrapperToolset):
    """Retain one call's observation while hooks receive the normal tool value."""

    outcome: ToolOutcome | None = None
    return_value: object = None

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[StepContext],
        tool: ToolsetTool[StepContext],
    ) -> object:
        # The inherited Pydantic AI method requires the parameter name ctx.
        self.outcome = await self.call_tool_outcome(name, tool_args, ctx, tool)
        self.return_value = _build_standard_tool_return_value(tool_outcome=self.outcome)
        return self.return_value


def get_current_step_run_context_required(*, boundary_name: str) -> RunContext[StepContext]:
    run_context = get_current_run_context()
    if run_context is None:
        raise NighthawkError(f"{boundary_name} requires an active RunContext")
    deps = getattr(run_context, "deps", None)
    if not isinstance(deps, StepContext):
        raise UnexpectedModelBehavior(f"{boundary_name} requires StepContext dependencies")
    return cast(RunContext[StepContext], run_context)


def _resolve_allowed_tool_names(
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
    with set_current_run_context(run_context):
        if run_context.deps._operation_state.failure is not None:
            raise run_context.deps._operation_state.failure
        parent_manager = run_context.tool_manager
        observed_toolset = _ObservedToolset(toolset.wrapped)
        manager = ToolManager(
            toolset=observed_toolset,
            ctx=run_context,
            tools={tool_name: tool},
            root_capability=with_tool_admission(parent_manager.root_capability if parent_manager is not None else None),
        )
        try:
            value = await manager.handle_call(ToolCallPart(tool_name, arguments, tool_call_id), wrap_validation_errors=False)
        except ValidationError as exception:
            if run_context.deps._operation_state.failure is not None:
                raise run_context.deps._operation_state.failure from run_context.deps._operation_state.failure.__cause__
            if observed_toolset.outcome is not None and (observed_toolset.outcome["error"] or {}).get("status") == "rejected":
                return ToolResultObservation(kind="tool_result", tool_outcome=observed_toolset.outcome)
            # MCP and SDK transports mark retry prompts as errors, so the text omits Pydantic AI's error wrapper.
            retry_text = ToolReturnPart(
                tool_name=tool_name,
                content=exception.errors(include_url=False, include_context=False),
                tool_call_id=tool_call_id,
                outcome="retried",
                retry=RetryInfo(cause="validation_error"),
            ).model_response_str(wrap_if_error=False)
            return RetryPromptObservation(kind="retry_prompt", retry_text=retry_text)
        except ModelRetry as exception:
            if run_context.deps._operation_state.failure is not None:
                raise run_context.deps._operation_state.failure from run_context.deps._operation_state.failure.__cause__
            if observed_toolset.outcome is not None and (observed_toolset.outcome["error"] or {}).get("status") == "rejected":
                return ToolResultObservation(kind="tool_result", tool_outcome=observed_toolset.outcome)
            return RetryPromptObservation(kind="retry_prompt", retry_text=exception.message)
        except ToolFailed as exception:
            if run_context.deps._operation_state.failure is not None:
                raise run_context.deps._operation_state.failure from run_context.deps._operation_state.failure.__cause__
            finished = None if exception.__cause__ is None else failed_operation(run_context.deps, exception=exception.__cause__)
            error: ToolError = (
                exception_feedback(finished) if finished is not None else {"kind": "exception", "message": exception.message, "guidance": None}
            )
            return ToolResultObservation(kind="tool_result", tool_outcome={"payload": None, "error": error})

        if run_context.deps._operation_state.failure is not None:
            raise run_context.deps._operation_state.failure
        recorded_error = observed_toolset.outcome["error"] if observed_toolset.outcome is not None else None
        if recorded_error is not None and recorded_error.get("status") == "rejected":
            # A capability may replace a return value, but cannot turn a refused body into a success.
            assert observed_toolset.outcome is not None
            tool_outcome = observed_toolset.outcome
        elif isinstance(value, ToolDenied):
            tool_outcome: ToolOutcome = {"payload": None, "error": {"kind": "oversight", "message": value.message, "guidance": None}}
        elif observed_toolset.outcome is not None and value is observed_toolset.return_value:
            tool_outcome = observed_toolset.outcome
        else:
            tool_outcome = _normalize_tool_success(value)

    return ToolResultObservation(kind="tool_result", tool_outcome=tool_outcome)


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
            tool_call_id = generate_ulid()
            from dataclasses import replace

            tool_run_context = replace(
                run_context,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )

            rendering_policy = get_rendering_policy()
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

            def build_trace_text(tool_handler_result: ToolHandlerResult) -> str:
                return build_tool_handler_result_trace_text(
                    tool_handler_result=tool_handler_result,
                    max_tokens=rendering_policy.tool_result_max_tokens,
                    encoding=encoding,
                    style=rendering_policy.json_renderer_style,
                )

            return await _run_tool_instrumented(
                tool_name=tool_name,
                argument_name_to_value=arguments,
                call=call,
                build_trace_text=build_trace_text,
                run_context=tool_run_context,
                tool_call_id=tool_call_id,
            )

        tool_name_to_handler[tool_name] = wrap_transported_tool(handler)

    return tool_name_to_handler


async def prepare_allowed_tools(
    *,
    model_request_parameters: ModelRequestParameters,
    configured_allowed_tool_names: tuple[str, ...] | None,
    visible_tools: list[Any],
) -> tuple[dict[str, Any], dict[str, ToolHandler], tuple[str, ...]]:
    tool_name_to_handler = await build_tool_name_to_handler(
        model_request_parameters=model_request_parameters,
        visible_tools=visible_tools,
    )
    available_tool_names = tuple(tool_name_to_handler.keys())

    allowed_tool_names = _resolve_allowed_tool_names(
        model_request_parameters=model_request_parameters,
        configured_allowed_tool_names=configured_allowed_tool_names,
        available_tool_names=available_tool_names,
    )

    tool_name_to_tool_definition = {
        name: tool_definition for name, tool_definition in model_request_parameters.tool_defs.items() if name in allowed_tool_names
    }
    tool_name_to_handler = {name: tool_name_to_handler[name] for name in allowed_tool_names}

    return tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names
