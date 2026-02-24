from __future__ import annotations

import json
import os
import textwrap
from datetime import datetime
from typing import Any, Literal, TypedDict, cast

from opentelemetry import context as otel_context
from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from ..tools.mcp_boundary import call_tool_for_claude_agent_sdk
from ..tools.registry import get_visible_tools
from . import BackendModelBase, ToolHandler

PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]

type SettingSource = Literal["user", "project", "local"]


def _normalize_timestamp_or_none(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.now(tz=datetime.now().astimezone().tzinfo)


class ClaudeAgentSdkModelSettings(TypedDict, total=False):
    permission_mode: PermissionMode
    setting_sources: list[SettingSource] | None
    allowed_tool_names: tuple[str, ...] | None
    claude_allowed_tool_names: tuple[str, ...] | None
    claude_max_turns: int
    working_directory: str


def _get_claude_agent_sdk_model_settings(model_settings: ModelSettings | None) -> ClaudeAgentSdkModelSettings:
    default_settings: ClaudeAgentSdkModelSettings = {
        "permission_mode": "default",
        "setting_sources": None,
        "allowed_tool_names": None,
        "claude_allowed_tool_names": None,
        "claude_max_turns": 50,
        "working_directory": "",
    }
    if model_settings is None:
        return default_settings

    model_settings_dict = cast(dict[str, Any], model_settings)

    permission_mode = model_settings_dict.get("permission_mode", "default")
    setting_sources_value = model_settings_dict.get("setting_sources")
    allowed_tool_names_value = model_settings_dict.get("allowed_tool_names")
    claude_allowed_tool_names_value = model_settings_dict.get("claude_allowed_tool_names")
    claude_max_turns_value = model_settings_dict.get("claude_max_turns")
    working_directory_value = model_settings_dict.get("working_directory")

    if not isinstance(permission_mode, str):
        raise UserError("permission_mode must be a string")

    if permission_mode not in {"default", "acceptEdits", "plan", "bypassPermissions"}:
        raise UserError("permission_mode must be one of: default, acceptEdits, plan, bypassPermissions")

    setting_sources: list[SettingSource] | None
    if setting_sources_value is None:
        setting_sources = None
    else:
        if not isinstance(setting_sources_value, (list, tuple)) or not all(isinstance(source, str) for source in setting_sources_value):
            raise UserError("setting_sources must be a list[SettingSource], tuple[SettingSource, ...], or None")
        allowed_setting_sources = {"user", "project", "local"}
        if not all(source in allowed_setting_sources for source in setting_sources_value):
            raise UserError("setting_sources must contain only: user, project, local")
        setting_sources = list(cast(tuple[SettingSource, ...], tuple(setting_sources_value)))

    allowed_tool_names: tuple[str, ...] | None
    if allowed_tool_names_value is None:
        allowed_tool_names = None
    else:
        if not isinstance(allowed_tool_names_value, (list, tuple)) or not all(isinstance(name, str) for name in allowed_tool_names_value):
            raise UserError("allowed_tool_names must be a list[str], tuple[str, ...], or None")
        allowed_tool_names = tuple(allowed_tool_names_value)

    claude_allowed_tool_names: tuple[str, ...] | None
    if claude_allowed_tool_names_value is None:
        claude_allowed_tool_names = None
    else:
        if not isinstance(claude_allowed_tool_names_value, (list, tuple)) or not all(isinstance(name, str) for name in claude_allowed_tool_names_value):
            raise UserError("claude_allowed_tool_names must be a list[str], tuple[str, ...], or None")
        claude_allowed_tool_names = tuple(claude_allowed_tool_names_value)

    if claude_max_turns_value is None:
        claude_max_turns = 50
    else:
        if not isinstance(claude_max_turns_value, int) or isinstance(claude_max_turns_value, bool):
            raise UserError("claude_max_turns must be an int")
        if claude_max_turns_value <= 0:
            raise UserError("claude_max_turns must be greater than 0")
        claude_max_turns = claude_max_turns_value

    if working_directory_value is None:
        working_directory = ""
    else:
        if not isinstance(working_directory_value, str) or working_directory_value.strip() == "":
            raise UserError("working_directory must be a non-empty string")
        if not os.path.isabs(working_directory_value):
            raise UserError("working_directory must be an absolute path")
        working_directory = working_directory_value

    return {
        "permission_mode": cast(PermissionMode, permission_mode),
        "setting_sources": setting_sources,
        "allowed_tool_names": allowed_tool_names,
        "claude_allowed_tool_names": claude_allowed_tool_names,
        "claude_max_turns": claude_max_turns,
        "working_directory": working_directory,
    }


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


def _normalize_claude_agent_sdk_usage_to_request_usage(usage: object) -> RequestUsage:
    request_usage = RequestUsage()
    if not isinstance(usage, dict):
        return request_usage

    input_tokens = usage.get("input_tokens")
    if isinstance(input_tokens, int):
        request_usage.input_tokens = input_tokens

    output_tokens = usage.get("output_tokens")
    if isinstance(output_tokens, int):
        request_usage.output_tokens = output_tokens

    cache_read_input_tokens = usage.get("cache_read_input_tokens")
    if isinstance(cache_read_input_tokens, int):
        request_usage.cache_read_tokens = cache_read_input_tokens

    cache_creation_input_tokens = usage.get("cache_creation_input_tokens")
    if isinstance(cache_creation_input_tokens, int):
        request_usage.cache_write_tokens = cache_creation_input_tokens

    return request_usage


class ClaudeCodeModel(BackendModelBase):
    def __init__(self, *, model_name: str | None = None) -> None:
        super().__init__(
            backend_label="Claude Agent SDK backend",
            profile=ModelProfile(
                supports_tools=True,
                supports_json_schema_output=True,
                supports_json_object_output=False,
                supports_image_output=False,
                default_structured_output_mode="native",
                supported_builtin_tools=frozenset([AbstractBuiltinTool]),
            ),
        )
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return f"claude-code:{self._model_name or 'default'}"

    @property
    def system(self) -> str:
        return "anthropic"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            ClaudeSDKClient,
            SdkMcpTool,
            create_sdk_mcp_server,
        )
        from claude_agent_sdk.types import AssistantMessage, ResultMessage  # pyright: ignore[reportMissingImports]

        model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)

        parent_otel_context = otel_context.get_current()

        _most_recent_request, system_prompt_text, user_prompt_text = self._prepare_common_request_parts(
            messages=messages,
            model_request_parameters=model_request_parameters,
        )

        claude_agent_sdk_model_settings = _get_claude_agent_sdk_model_settings(model_settings)

        tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names = await self._prepare_allowed_tools(
            model_request_parameters=model_request_parameters,
            configured_allowed_tool_names=claude_agent_sdk_model_settings.get("allowed_tool_names"),
            visible_tools=get_visible_tools(),
        )

        mcp_tools: list[Any] = []
        for tool_name, handler in tool_name_to_handler.items():
            tool_definition = tool_name_to_tool_definition.get(tool_name)
            if tool_definition is None:
                raise UnexpectedModelBehavior(f"Tool definition missing for {tool_name!r}")

            async def wrapped_handler(arguments: dict[str, Any], *, tool_handler: ToolHandler = handler) -> dict[str, Any]:
                return await call_tool_for_claude_agent_sdk(
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_handler=tool_handler,
                    parent_otel_context=parent_otel_context,
                )

            mcp_tools.append(
                SdkMcpTool(
                    name=tool_name,
                    description=tool_definition.description or "",
                    input_schema=tool_definition.parameters_json_schema,
                    handler=wrapped_handler,
                )
            )

        sdk_server = create_sdk_mcp_server("nighthawk", tools=mcp_tools)

        allowed_tools_for_claude = [f"mcp__nighthawk__{tool_name}" for tool_name in allowed_tool_names]

        claude_allowed_tool_names = claude_agent_sdk_model_settings.get("claude_allowed_tool_names") or ()
        merged_allowed_tools: list[str] = []
        seen_allowed_tools: set[str] = set()
        for tool_name in [*claude_allowed_tool_names, *allowed_tools_for_claude]:
            if tool_name in seen_allowed_tools:
                continue
            merged_allowed_tools.append(tool_name)
            seen_allowed_tools.add(tool_name)

        working_directory = claude_agent_sdk_model_settings.get("working_directory") or ""

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

        options_keyword_arguments: dict[str, Any] = {
            "tools": {
                "type": "preset",
                "preset": "claude_code",
            },
            "allowed_tools": merged_allowed_tools,
            "system_prompt": {
                "type": "preset",
                "preset": "claude_code",
                "append": system_prompt_text,
            },
            "mcp_servers": {"nighthawk": sdk_server},
            "permission_mode": claude_agent_sdk_model_settings.get("permission_mode", "default"),
            "model": self._model_name,
            "setting_sources": claude_agent_sdk_model_settings.get("setting_sources"),
            "max_turns": claude_agent_sdk_model_settings.get("claude_max_turns", 50),
            "output_format": _build_json_schema_output_format(model_request_parameters),
        }

        if working_directory:
            options_keyword_arguments["cwd"] = working_directory

        options = ClaudeAgentOptions(**options_keyword_arguments)

        assistant_model_name: str | None = None
        result_message: ResultMessage | None = None

        # Claude Code sets the CLAUDECODE environment variable for nested sessions.
        # When the variable is set, the Claude Code CLI refuses to launch.
        claude_code_nested_environment_value = os.environ.pop("CLAUDECODE", None)
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(user_prompt_text)

                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        assistant_model_name = message.model
                    elif isinstance(message, ResultMessage):
                        result_message = message
        finally:
            if claude_code_nested_environment_value is not None:
                os.environ["CLAUDECODE"] = claude_code_nested_environment_value

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

        return ModelResponse(
            parts=[TextPart(content=output_text)],
            model_name=assistant_model_name,
            timestamp=_normalize_timestamp_or_none(getattr(result_message, "timestamp", None)),
            usage=_normalize_claude_agent_sdk_usage_to_request_usage(getattr(result_message, "usage", None)),
        )
