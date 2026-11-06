from __future__ import annotations

import json
from typing import Any

from opentelemetry import context as otel_context
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings

from nighthawk.adapters.pydantic_ai._rendering import to_jsonable_value
from nighthawk.adapters.pydantic_ai.models._claude_code_usage import normalize_claude_code_usage
from nighthawk.adapters.pydantic_ai.models.base import BackendModelBase, append_text_projected_tool_result_preview_prompt
from nighthawk.adapters.pydantic_ai.models.claude_code_settings import (
    NIGHTHAWK_MCP_TOOL_PREFIX,
    TOOL_ACCESS_SYSTEM_PROMPT_FRAGMENT,
    ClaudeCodeModelSettings,
    output_json_schema,
)
from nighthawk.adapters.pydantic_ai.models.mcp_boundary import call_tool_for_claude_code_sdk
from nighthawk.adapters.pydantic_ai.models.text_projection import resolve_text_projection_staging_root_directory
from nighthawk.adapters.pydantic_ai.models.tool_bridge import ToolHandler
from nighthawk.adapters.pydantic_ai.presentation import get_rendering_policy
from nighthawk.adapters.pydantic_ai.tools.declarations import get_visible_tools


class ClaudeCodeSdkModelSettings(ClaudeCodeModelSettings):
    """Settings for the Claude Code SDK backend."""


def _build_json_schema_output_format(model_request_parameters: ModelRequestParameters) -> dict[str, Any] | None:
    schema = output_json_schema(model_request_parameters)
    return None if schema is None else {"type": "json_schema", "schema": schema}


class ClaudeCodeSdkModel(BackendModelBase):
    """Pydantic AI model that delegates to Claude Code via the Claude Agent SDK."""

    def __init__(self, *, model_name: str | None = None) -> None:
        super().__init__(
            backend_label="Claude Code SDK backend",
            profile=ModelProfile(
                supports_tools=True,
                supports_json_schema_output=True,
                supports_json_object_output=False,
                supports_image_output=False,
                default_structured_output_mode="native",
                supported_native_tools=frozenset(),
            ),
        )
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return f"claude-code-sdk:{self._model_name or 'default'}"

    @property
    def system(self) -> str:
        return "anthropic"

    async def _request(
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
        from claude_agent_sdk.types import AssistantMessage, Message, ResultMessage  # pyright: ignore[reportMissingImports]

        model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)
        claude_code_model_settings = ClaudeCodeSdkModelSettings.from_model_settings(model_settings)
        staging_root_directory = resolve_text_projection_staging_root_directory(
            working_directory=claude_code_model_settings.working_directory,
        )
        tool_result_rendering_policy = get_rendering_policy()
        parent_otel_context = otel_context.get_current()

        prepared_projected_request = self._prepare_text_projected_request(
            messages=messages,
            model_request_parameters=model_request_parameters,
            staging_root_directory=staging_root_directory,
            empty_prompt_exception_factory=UnexpectedModelBehavior,
        )
        projected_request = prepared_projected_request.projected_request
        try:
            system_prompt_text = prepared_projected_request.system_prompt_text
            user_prompt_text = prepared_projected_request.user_prompt_text

            tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names = await self._prepare_allowed_tools(
                model_request_parameters=model_request_parameters,
                configured_allowed_tool_names=claude_code_model_settings.allowed_tool_names,
                visible_tools=get_visible_tools(),
            )

            if allowed_tool_names:
                system_prompt_text = append_text_projected_tool_result_preview_prompt(system_prompt_text=system_prompt_text)

            mcp_tools: list[Any] = []
            for tool_name, handler in tool_name_to_handler.items():
                tool_definition = tool_name_to_tool_definition.get(tool_name)
                if tool_definition is None:
                    raise UnexpectedModelBehavior(f"Tool definition missing for {tool_name!r}")

                async def wrapped_handler(
                    arguments: dict[str, Any],
                    *,
                    tool_handler: ToolHandler = handler,
                    bound_tool_name: str = tool_name,
                ) -> dict[str, Any]:
                    return await call_tool_for_claude_code_sdk(
                        tool_name=bound_tool_name,
                        arguments=arguments,
                        tool_handler=tool_handler,
                        parent_otel_context=parent_otel_context,
                        rendering_policy=tool_result_rendering_policy,
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

            allowed_tools_for_claude = [f"{NIGHTHAWK_MCP_TOOL_PREFIX}{tool_name}" for tool_name in allowed_tool_names]

            claude_allowed_tool_names = claude_code_model_settings.claude_allowed_tool_names or ()
            merged_allowed_tools = list(dict.fromkeys([*claude_allowed_tool_names, *allowed_tools_for_claude]))

            working_directory = claude_code_model_settings.working_directory

            if allowed_tool_names:
                system_prompt_text += TOOL_ACCESS_SYSTEM_PROMPT_FRAGMENT

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
                "model": self._model_name,
                "output_format": _build_json_schema_output_format(model_request_parameters),
            }

            if claude_code_model_settings.permission_mode is not None:
                options_keyword_arguments["permission_mode"] = claude_code_model_settings.permission_mode
            if claude_code_model_settings.setting_sources is not None:
                options_keyword_arguments["setting_sources"] = claude_code_model_settings.setting_sources
            if claude_code_model_settings.max_turns is not None:
                options_keyword_arguments["max_turns"] = claude_code_model_settings.max_turns
            if claude_code_model_settings.max_budget_usd is not None:
                options_keyword_arguments["max_budget_usd"] = claude_code_model_settings.max_budget_usd
            if working_directory is not None:
                options_keyword_arguments["cwd"] = working_directory

            options = ClaudeAgentOptions(**options_keyword_arguments)

            assistant_model_name: str | None = None
            result_message: ResultMessage | None = None
            result_messages: list[Message] = []

            async with ClaudeSDKClient(options=options) as client:
                await client.query(user_prompt_text)

                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        assistant_model_name = message.model
                    elif isinstance(message, ResultMessage):
                        result_message = message
                    result_messages.append(message)

            if result_message is None:
                raise UnexpectedModelBehavior("Claude Code backend did not produce a result message")

            if result_message.is_error:
                error_text = result_message.result or "Claude Code backend reported an error"
                result_messages_json = json.dumps(to_jsonable_value(result_messages), ensure_ascii=False)
                raise UnexpectedModelBehavior(
                    f"{error_text}\nresult_message_json={result_messages_json}\noutput_format={options_keyword_arguments['output_format']}"
                )

            structured_output = result_message.structured_output
            if structured_output is None:
                if model_request_parameters.output_object is not None:
                    result_messages_json = json.dumps(to_jsonable_value(result_messages), ensure_ascii=False)
                    raise UnexpectedModelBehavior(f"Claude Code backend did not return structured output\nresult_message_json={result_messages_json}")

                if result_message.result is None:
                    raise UnexpectedModelBehavior("Claude Code backend did not return text output")
                output_text = result_message.result
            else:
                output_text = json.dumps(structured_output, ensure_ascii=False)

            return ModelResponse(
                parts=[TextPart(content=output_text)],
                model_name=assistant_model_name,
                provider_name="claude-code-sdk",
                usage=normalize_claude_code_usage(result_message.usage),
            )
        finally:
            projected_request.cleanup()
