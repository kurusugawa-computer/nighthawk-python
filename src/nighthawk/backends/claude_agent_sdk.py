from __future__ import annotations

import json
import textwrap
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict, cast

from claude_agent_sdk import ClaudeAgentOptions, SdkMcpTool, create_sdk_mcp_server
from claude_agent_sdk import query as claude_agent_sdk_query
from claude_agent_sdk.types import AssistantMessage, PermissionMode, ResultMessage
from pydantic_ai import RunContext
from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from ..execution.environment import get_environment
from ..tools import get_visible_tools
from ..tools.assignment import serialize_value_to_json_text


class ClaudeAgentSdkModelSettings(TypedDict, total=False):
    permission_mode: PermissionMode
    allowed_tool_names: tuple[str, ...] | None


def _get_claude_agent_sdk_model_settings(model_settings: ModelSettings | None) -> ClaudeAgentSdkModelSettings:
    default_settings: ClaudeAgentSdkModelSettings = {"permission_mode": "default"}
    if model_settings is None:
        return default_settings

    permission_mode = model_settings.get("permission_mode", "default")
    allowed_tool_names_value = model_settings.get("allowed_tool_names")

    if not isinstance(permission_mode, str):
        raise UserError("permission_mode must be a string")

    if permission_mode not in {"default", "acceptEdits", "plan", "bypassPermissions"}:
        raise UserError("permission_mode must be one of: default, acceptEdits, plan, bypassPermissions")

    allowed_tool_names: tuple[str, ...] | None
    if allowed_tool_names_value is None:
        allowed_tool_names = None
    else:
        if not isinstance(allowed_tool_names_value, tuple) or not all(isinstance(name, str) for name in allowed_tool_names_value):
            raise UserError("allowed_tool_names must be a tuple[str, ...] or None")
        allowed_tool_names = allowed_tool_names_value

    return {
        "permission_mode": cast(PermissionMode, permission_mode),
        "allowed_tool_names": allowed_tool_names,
    }


def _find_most_recent_model_request(messages: list[ModelMessage]) -> ModelRequest:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return message
    raise UnexpectedModelBehavior("No ModelRequest found in message history")


def _collect_system_prompt_text(model_request: ModelRequest) -> str:
    parts: list[str] = []
    for part in model_request.parts:
        if isinstance(part, SystemPromptPart):
            if part.content:
                parts.append(part.content)
    return "\n\n".join(parts)


def _collect_user_prompt_text(model_request: ModelRequest) -> str:
    parts: list[str] = []
    for part in model_request.parts:
        if isinstance(part, UserPromptPart):
            if isinstance(part.content, str):
                parts.append(part.content)
            else:
                # This backend does not support image/audio/document user content yet.
                raise UserError("Claude Agent SDK backend does not support non-text user prompts")
        elif isinstance(part, RetryPromptPart):
            parts.append(part.model_response())
        elif isinstance(part, ToolReturnPart):
            # Fail-closed: tool-return parts indicate Pydantic AI tool loop state, which this backend does not model.
            raise UserError("Claude Agent SDK backend does not support tool-return parts")

    return "\n\n".join(p for p in parts if p)


def _serialize_value_to_json_text(value: object) -> str:
    return serialize_value_to_json_text(value)


def _build_json_schema_output_format(model_request_parameters: ModelRequestParameters) -> dict[str, Any] | None:
    output_object = model_request_parameters.output_object
    if output_object is None:
        return None

    schema = dict(output_object.json_schema)
    if output_object.name:
        schema["title"] = output_object.name
    if output_object.description:
        schema["description"] = output_object.description

    return {"type": "json_schema", "schema": schema}


class ClaudeAgentSdkModel(Model):
    def __init__(self) -> None:
        super().__init__(
            profile=ModelProfile(
                supports_tools=True,
                supports_json_schema_output=True,
                supports_json_object_output=False,
                supports_image_output=False,
                default_structured_output_mode="native",
                supported_builtin_tools=frozenset([AbstractBuiltinTool]),
            )
        )
        _ = self

    @property
    def model_name(self) -> str:
        return "claude-agent-sdk:outside"

    @property
    def system(self) -> str:
        # Claude Code ultimately uses Anthropic models.
        return "anthropic"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)

        if model_request_parameters.builtin_tools:
            raise UserError("Claude Agent SDK backend does not support builtin tools")

        if model_request_parameters.allow_image_output:
            raise UserError("Claude Agent SDK backend does not support image output")

        most_recent_request = _find_most_recent_model_request(messages)

        system_prompt_text = _collect_system_prompt_text(most_recent_request)

        instructions = self._get_instructions(messages, model_request_parameters)
        if instructions:
            if system_prompt_text:
                system_prompt_text = "\n\n".join([system_prompt_text, instructions])
            else:
                system_prompt_text = instructions

        tool_name_to_handler = await self._build_tool_name_to_handler(model_request_parameters)

        mcp_tools: list[SdkMcpTool[Any]] = []
        for tool_name, handler in tool_name_to_handler.items():
            tool_def = model_request_parameters.tool_defs.get(tool_name)
            if tool_def is None:
                raise UnexpectedModelBehavior(f"Tool definition missing for {tool_name!r}")

            async def wrapped_handler(arguments: dict[str, Any], *, _handler: Callable[[dict[str, Any]], Awaitable[str]] = handler) -> dict[str, Any]:
                result_text = await _handler(arguments)
                return {"content": [{"type": "text", "text": result_text}]}

            mcp_tools.append(
                SdkMcpTool(
                    name=tool_name,
                    description=tool_def.description or "",
                    input_schema=tool_def.parameters_json_schema,
                    handler=wrapped_handler,
                )
            )

        sdk_server = create_sdk_mcp_server("nighthawk", tools=mcp_tools)

        claude_agent_sdk_model_settings = _get_claude_agent_sdk_model_settings(model_settings)

        allowed_tool_names = self._resolve_allowed_tool_names(
            model_request_parameters,
            claude_agent_sdk_model_settings=claude_agent_sdk_model_settings,
            available_tool_names=tuple(tool_name_to_handler.keys()),
        )
        allowed_tools_for_claude = [f"mcp__nighthawk__{tool_name}" for tool_name in allowed_tool_names]

        try:
            working_directory = get_environment().workspace_root
        except Exception:
            working_directory = None

        if allowed_tool_names:
            system_prompt_text = "\n\n".join(
                [
                    system_prompt_text,
                    textwrap.dedent("""
                    Tool access:
                    - Nighthawk tools are exposed via MCP; tool names are prefixed with: mcp__nighthawk__
                    - If you want to call nh_eval(...), call: mcp__nighthawk__nh_eval
                    - If you want to call nh_assign(...), call: mcp__nighthawk__nh_assign
                    """),
                ]
            )

        options = ClaudeAgentOptions(
            tools=[],
            allowed_tools=allowed_tools_for_claude,
            system_prompt=system_prompt_text,
            mcp_servers={"nighthawk": sdk_server},
            permission_mode=claude_agent_sdk_model_settings.get("permission_mode", "default"),
            cwd=working_directory,
            setting_sources=[],
            max_turns=50,
            output_format=_build_json_schema_output_format(model_request_parameters),
        )

        user_prompt_text = _collect_user_prompt_text(most_recent_request)
        if user_prompt_text.strip() == "":
            raise UserError("Claude Agent SDK backend requires a non-empty user prompt")

        prompt_text = user_prompt_text

        assistant_model_name: str | None = None
        result_message: ResultMessage | None = None

        prompt_stream = _single_user_message_stream(prompt_text)

        async for message in claude_agent_sdk_query(prompt=prompt_stream, options=options):
            if isinstance(message, AssistantMessage):
                assistant_model_name = message.model
            elif isinstance(message, ResultMessage):
                result_message = message

        if result_message is None:
            raise UnexpectedModelBehavior("Claude Agent SDK backend did not produce a result message")

        if result_message.is_error:
            error_text = result_message.result or "Claude Agent SDK backend reported an error"
            raise UnexpectedModelBehavior(str(error_text))

        structured_output = result_message.structured_output
        if structured_output is None:
            if model_request_parameters.output_object is not None:
                raise UnexpectedModelBehavior("Claude Agent SDK backend did not return structured output")

            if result_message.result is None:
                raise UnexpectedModelBehavior("Claude Agent SDK backend did not return text output")
            output_text = result_message.result
        else:
            output_text = json.dumps(structured_output, ensure_ascii=False)

        provider_details: dict[str, Any] = {
            "claude_agent_sdk": {
                "session_id": result_message.session_id,
                "usage": result_message.usage,
            }
        }

        return ModelResponse(
            parts=[TextPart(content=output_text)],
            usage=RequestUsage(),
            model_name=assistant_model_name or self.model_name,
            provider_name="claude-agent-sdk",
            provider_details=provider_details,
        )

    def _resolve_allowed_tool_names(
        self,
        model_request_parameters: ModelRequestParameters,
        *,
        claude_agent_sdk_model_settings: ClaudeAgentSdkModelSettings,
        available_tool_names: tuple[str, ...],
    ) -> tuple[str, ...]:
        configured_allowlist = claude_agent_sdk_model_settings.get("allowed_tool_names")
        if configured_allowlist is None:
            return tuple(name for name in (tool_def.name for tool_def in model_request_parameters.function_tools) if name in available_tool_names)

        unknown = [name for name in configured_allowlist if name not in available_tool_names]
        if unknown:
            unknown_list = ", ".join(repr(name) for name in unknown)
            raise ValueError(f"Configured allowed_tool_names includes unknown tools: {unknown_list}")

        return configured_allowlist

    async def _build_tool_name_to_handler(
        self,
        model_request_parameters: ModelRequestParameters,
    ) -> dict[str, Callable[[dict[str, Any]], Awaitable[str]]]:
        current_run_context = _get_current_run_context_or_none()
        if current_run_context is None:
            raise UnexpectedModelBehavior("Claude Agent SDK backend requires a Pydantic AI RunContext")

        visible_tools = get_visible_tools()
        toolset = _build_toolset(visible_tools)
        toolset_tools = await toolset.get_tools(current_run_context)

        tool_name_to_handler: dict[str, Callable[[dict[str, Any]], Awaitable[str]]] = {}

        function_tool_names = {tool_def.name for tool_def in model_request_parameters.function_tools}

        for tool_name, tool in toolset_tools.items():
            if tool_name not in function_tool_names:
                continue

            async def handler(
                arguments: dict[str, Any],
                *,
                tool_name: str = tool_name,
                tool: Any = tool,
                run_context: RunContext[Any] = current_run_context,
            ) -> str:
                validated_arguments = tool.args_validator.validate_python(arguments)

                tool_call_id = str(uuid.uuid4())
                tool_run_context = _replace_run_context_for_tool(
                    run_context,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                )

                result = await tool.toolset.call_tool(tool_name, validated_arguments, tool_run_context, tool)
                return _serialize_value_to_json_text(result)

            tool_name_to_handler[tool_name] = handler

        return tool_name_to_handler


def _single_user_message_stream(prompt_text: str) -> Any:
    async def iterator():
        yield {
            "type": "user",
            "message": {"role": "user", "content": prompt_text},
            "parent_tool_use_id": None,
            "session_id": "nighthawk",
        }

    return iterator()


def _build_toolset(tools: list[Any]) -> Any:
    from pydantic_ai.toolsets.function import FunctionToolset

    return FunctionToolset(tools)


def _get_current_run_context_or_none() -> RunContext[Any] | None:
    from pydantic_ai._run_context import get_current_run_context

    return get_current_run_context()


def _replace_run_context_for_tool(
    run_context: RunContext[Any],
    *,
    tool_name: str,
    tool_call_id: str,
) -> RunContext[Any]:
    from dataclasses import replace

    return replace(
        run_context,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )
