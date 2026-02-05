from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from claude_agent_sdk.types import PermissionMode
else:
    PermissionMode = str  # type: ignore[assignment]

from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from ..execution.environment import get_environment
from ..tools import get_visible_tools
from . import BackendModelBase, ToolHandler


class ClaudeAgentSdkModelSettings(TypedDict, total=False):
    permission_mode: PermissionMode
    allowed_tool_names: tuple[str, ...] | None


def _get_claude_agent_sdk_model_settings(model_settings: ModelSettings | None) -> ClaudeAgentSdkModelSettings:
    default_settings: ClaudeAgentSdkModelSettings = {"permission_mode": "default", "allowed_tool_names": None}
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
        from claude_agent_sdk import ClaudeAgentOptions, SdkMcpTool, create_sdk_mcp_server
        from claude_agent_sdk import query as claude_agent_sdk_query
        from claude_agent_sdk.types import AssistantMessage, ResultMessage

        model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)

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
                result_text = await tool_handler(arguments)
                return {"content": [{"type": "text", "text": result_text}]}

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
            model=self._model_name,
            cwd=working_directory,
            setting_sources=[],
            max_turns=50,
            output_format=_build_json_schema_output_format(model_request_parameters),
        )

        assistant_model_name: str | None = None
        result_message: ResultMessage | None = None

        prompt_stream = _single_user_message_stream(user_prompt_text)

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

        usage = _normalize_claude_agent_sdk_usage_to_request_usage(result_message.usage)

        provider_details: dict[str, Any] = {
            "claude_code": {
                "session_id": result_message.session_id,
            }
        }

        return ModelResponse(
            parts=[TextPart(content=output_text)],
            usage=usage,
            model_name=assistant_model_name or self.model_name,
            provider_name="claude-code",
            provider_details=provider_details,
        )


def _single_user_message_stream(prompt_text: str) -> Any:
    async def iterator():
        yield {
            "type": "user",
            "message": {"role": "user", "content": prompt_text},
            "parent_tool_use_id": None,
            "session_id": "nighthawk",
        }

    return iterator()
