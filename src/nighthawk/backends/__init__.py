from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, SystemPromptPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.toolsets.function import FunctionToolset

from ..tools.assignment import serialize_value_to_json_text

type ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


def find_most_recent_model_request(messages: list[ModelMessage]) -> ModelRequest:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return message
    raise UnexpectedModelBehavior("No ModelRequest found in message history")


def collect_system_prompt_text(model_request: ModelRequest) -> str:
    parts: list[str] = []
    for part in model_request.parts:
        if isinstance(part, SystemPromptPart) and part.content:
            parts.append(part.content)
    return "\n\n".join(parts)


def collect_user_prompt_text(model_request: ModelRequest, *, backend_label: str) -> str:
    parts: list[str] = []
    for part in model_request.parts:
        if isinstance(part, UserPromptPart):
            if isinstance(part.content, str):
                parts.append(part.content)
            else:
                raise UserError(f"{backend_label} does not support non-text user prompts")
        elif isinstance(part, RetryPromptPart):
            parts.append(part.model_response())
        elif isinstance(part, ToolReturnPart):
            raise UserError(f"{backend_label} does not support tool-return parts")

    return "\n\n".join(p for p in parts if p)


def get_current_run_context_or_none() -> RunContext[Any] | None:
    from pydantic_ai._run_context import get_current_run_context

    return get_current_run_context()


def generate_tool_call_id() -> str:
    return str(uuid.uuid4())


def replace_run_context_for_tool(
    run_context: RunContext[Any],
    *,
    tool_name: str,
    tool_call_id: str,
) -> RunContext[Any]:
    return replace(
        run_context,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )


def resolve_allowed_tool_names(
    *,
    model_request_parameters: ModelRequestParameters,
    configured_allowed_tool_names: tuple[str, ...] | None,
    available_tool_names: tuple[str, ...],
) -> tuple[str, ...]:
    if configured_allowed_tool_names is None:
        return tuple(name for name in (tool_definition.name for tool_definition in model_request_parameters.function_tools) if name in available_tool_names)

    unknown = [name for name in configured_allowed_tool_names if name not in available_tool_names]
    if unknown:
        unknown_list = ", ".join(repr(name) for name in unknown)
        raise ValueError(f"Configured allowed_tool_names includes unknown tools: {unknown_list}")

    return configured_allowed_tool_names


async def build_tool_name_to_handler(
    *,
    model_request_parameters: ModelRequestParameters,
    visible_tools: list[Any],
    backend_label: str,
) -> dict[str, ToolHandler]:
    run_context = get_current_run_context_or_none()
    if run_context is None:
        raise UnexpectedModelBehavior(f"{backend_label} requires a Pydantic AI RunContext")

    toolset = FunctionToolset(visible_tools)
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
            run_context: RunContext[Any] = run_context,
        ) -> str:
            validated_arguments = tool.args_validator.validate_python(arguments)

            tool_call_id = generate_tool_call_id()
            tool_run_context = replace_run_context_for_tool(
                run_context,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )

            result = await tool.toolset.call_tool(tool_name, validated_arguments, tool_run_context, tool)
            return serialize_value_to_json_text(result)

        tool_name_to_handler[tool_name] = handler

    return tool_name_to_handler


class BackendModelBase(Model):
    """Shared request prelude for backends that expose Nighthawk tools via Pydantic AI FunctionToolset.

    Provider-specific backends should:
    - call `prepare_request(...)` and then `_prepare_common_request_parts(...)`
    - call `_prepare_allowed_tools(...)` to get filtered tool definitions/handlers
    - handle provider-specific transport/execution and convert to `ModelResponse`
    """

    backend_label: str

    def __init__(self, *, backend_label: str, profile: Any) -> None:
        super().__init__(profile=profile)
        self.backend_label = backend_label

    def _prepare_common_request_parts(
        self,
        *,
        messages: list[ModelMessage],
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelRequest, str, str]:
        if model_request_parameters.builtin_tools:
            raise UserError(f"{self.backend_label} does not support builtin tools")

        if model_request_parameters.allow_image_output:
            raise UserError(f"{self.backend_label} does not support image output")

        model_request = find_most_recent_model_request(messages)

        system_prompt_text = collect_system_prompt_text(model_request)

        instructions = self._get_instructions(messages, model_request_parameters)
        if instructions:
            if system_prompt_text:
                system_prompt_text = "\n\n".join([system_prompt_text, instructions])
            else:
                system_prompt_text = instructions

        user_prompt_text = collect_user_prompt_text(model_request, backend_label=self.backend_label)
        if user_prompt_text.strip() == "":
            raise UserError(f"{self.backend_label} requires a non-empty user prompt")

        return model_request, system_prompt_text, user_prompt_text

    async def _prepare_allowed_tools(
        self,
        *,
        model_request_parameters: ModelRequestParameters,
        configured_allowed_tool_names: tuple[str, ...] | None,
        visible_tools: list[Any],
    ) -> tuple[dict[str, Any], dict[str, ToolHandler], tuple[str, ...]]:
        tool_name_to_handler = await build_tool_name_to_handler(
            model_request_parameters=model_request_parameters,
            visible_tools=visible_tools,
            backend_label=self.backend_label,
        )
        available_tool_names = tuple(tool_name_to_handler.keys())

        allowed_tool_names = resolve_allowed_tool_names(
            model_request_parameters=model_request_parameters,
            configured_allowed_tool_names=configured_allowed_tool_names,
            available_tool_names=available_tool_names,
        )

        tool_name_to_tool_definition = {name: tool_definition for name, tool_definition in model_request_parameters.tool_defs.items() if name in allowed_tool_names}
        tool_name_to_handler = {name: tool_name_to_handler[name] for name in allowed_tool_names}

        return tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names


__all__ = [
    "BackendModelBase",
    "ToolHandler",
    "build_tool_name_to_handler",
    "collect_system_prompt_text",
    "collect_user_prompt_text",
    "find_most_recent_model_request",
    "generate_tool_call_id",
    "get_current_run_context_or_none",
    "replace_run_context_for_tool",
    "resolve_allowed_tool_names",
]
