from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime
from typing import Any, cast

from opentelemetry import context as otel_context
from pydantic_ai import RunContext
from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from ..json_renderer import to_jsonable_value
from ..runtime.step_context import DEFAULT_TOOL_RESULT_RENDERING_POLICY, StepContext, resolve_tool_result_rendering_policy
from ..tools.registry import get_visible_tools
from .base import BackendModelBase
from .claude_code_settings import ClaudeCodeModelSettings
from .mcp_boundary import call_tool_for_claude_code_sdk
from .text_projection import TextProjectedRequest, resolve_text_projection_staging_root_directory
from .tool_bridge import ToolHandler


def _normalize_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.now(tz=datetime.now().astimezone().tzinfo)


class ClaudeCodeSdkModelSettings(ClaudeCodeModelSettings):
    """Settings for the Claude Code SDK backend.

    Attributes:
        claude_allowed_tool_names: Additional Claude Code native tool names to allow.
    """

    claude_allowed_tool_names: tuple[str, ...] | None = None


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


def _normalize_claude_code_sdk_usage_to_request_usage(usage: object) -> RequestUsage:
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


def _serialize_result_message_to_json(result_message: object) -> str:
    result_message_model_dump_json = getattr(result_message, "model_dump_json", None)
    if callable(result_message_model_dump_json):
        try:
            result_message_json = result_message_model_dump_json()
            if isinstance(result_message_json, str):
                return result_message_json
        except Exception:
            pass

    result_message_model_dump = getattr(result_message, "model_dump", None)
    if callable(result_message_model_dump):
        with contextlib.suppress(Exception):
            result_message = result_message_model_dump()

    try:
        return json.dumps(to_jsonable_value(result_message), ensure_ascii=False)
    except Exception:
        return json.dumps({"result_message_repr": repr(result_message)}, ensure_ascii=False)


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
                supported_builtin_tools=frozenset([AbstractBuiltinTool]),
            ),
        )
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return f"claude-code-sdk:{self._model_name or 'default'}"

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
        from claude_agent_sdk.types import AssistantMessage, Message, ResultMessage  # pyright: ignore[reportMissingImports]

        model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)
        claude_code_model_settings = ClaudeCodeSdkModelSettings.from_model_settings(model_settings)
        staging_root_directory = resolve_text_projection_staging_root_directory(
            working_directory=claude_code_model_settings.working_directory,
        )
        from pydantic_ai._run_context import get_current_run_context

        parent_run_context = get_current_run_context()
        if parent_run_context is None:
            tool_result_rendering_policy = DEFAULT_TOOL_RESULT_RENDERING_POLICY
        else:
            typed_parent_run_context = cast(RunContext[StepContext], parent_run_context)
            if not isinstance(typed_parent_run_context.deps, StepContext):
                raise UnexpectedModelBehavior("Claude Code SDK backend requires StepContext dependencies")
            tool_result_rendering_policy = resolve_tool_result_rendering_policy(typed_parent_run_context.deps.tool_result_rendering_policy)

        parent_otel_context = otel_context.get_current()

        projected_request: TextProjectedRequest | None = None

        prepared_projected_request = self._prepare_text_projected_request(
            messages=messages,
            model_request_parameters=model_request_parameters,
            staging_root_directory=staging_root_directory,
            empty_prompt_exception_factory=UnexpectedModelBehavior,
        )
        try:
            projected_request = prepared_projected_request.projected_request
            system_prompt_text = prepared_projected_request.system_prompt_text
            user_prompt_text = prepared_projected_request.user_prompt_text

            tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names = await self._prepare_allowed_tools(
                model_request_parameters=model_request_parameters,
                configured_allowed_tool_names=claude_code_model_settings.allowed_tool_names,
                visible_tools=get_visible_tools(),
            )

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

            allowed_tools_for_claude = [f"mcp__nighthawk__{tool_name}" for tool_name in allowed_tool_names]

            claude_allowed_tool_names = claude_code_model_settings.claude_allowed_tool_names or ()
            merged_allowed_tools: list[str] = []
            seen_allowed_tools: set[str] = set()
            for tool_name in [*claude_allowed_tool_names, *allowed_tools_for_claude]:
                if tool_name in seen_allowed_tools:
                    continue
                merged_allowed_tools.append(tool_name)
                seen_allowed_tools.add(tool_name)

            working_directory = claude_code_model_settings.working_directory

            if allowed_tool_names:
                system_prompt_text = "\n".join(
                    [
                        system_prompt_text,
                        "",
                        "Tool access:",
                        "- Nighthawk tools are exposed via MCP; tool names are prefixed with: mcp__nighthawk__",
                        "- Example: to call nh_eval(...), use: mcp__nighthawk__nh_eval",
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
                "model": self._model_name,
                "output_format": _build_json_schema_output_format(model_request_parameters),
            }

            if claude_code_model_settings.permission_mode is not None:
                options_keyword_arguments["permission_mode"] = claude_code_model_settings.permission_mode
            if claude_code_model_settings.setting_sources is not None:
                options_keyword_arguments["setting_sources"] = claude_code_model_settings.setting_sources
            if claude_code_model_settings.max_turns is not None:
                options_keyword_arguments["max_turns"] = claude_code_model_settings.max_turns
            if working_directory:
                options_keyword_arguments["cwd"] = working_directory

            options = ClaudeAgentOptions(**options_keyword_arguments)

            assistant_model_name: str | None = None
            result_message: ResultMessage | None = None
            result_messages: list[Message] = []

            # Claude Code sets the CLAUDECODE environment variable for nested sessions.
            # When the variable is set, the Claude Code CLI refuses to launch.
            # This modifies the process-global environment, which is unavoidable because
            # the Claude Agent SDK inherits environment variables from the parent process.
            saved_claudecode_value = os.environ.pop("CLAUDECODE", None)

            try:
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(user_prompt_text)

                    async for message in client.receive_response():
                        if isinstance(message, AssistantMessage):
                            assistant_model_name = message.model
                        elif isinstance(message, ResultMessage):
                            result_message = message
                        result_messages.append(message)
            finally:
                if saved_claudecode_value is not None:
                    os.environ["CLAUDECODE"] = saved_claudecode_value

            if result_message is None:
                raise UnexpectedModelBehavior("Claude Code backend did not produce a result message")

            if result_message.is_error:
                error_text = result_message.result or "Claude Code backend reported an error"
                result_messages_json = _serialize_result_message_to_json(result_messages)
                raise UnexpectedModelBehavior(
                    f"{error_text}\nresult_message_json={result_messages_json}\noutput_format={options_keyword_arguments['output_format']}"
                )

            structured_output = result_message.structured_output
            if structured_output is None:
                if model_request_parameters.output_object is not None:
                    result_messages_json = _serialize_result_message_to_json(result_messages)
                    raise UnexpectedModelBehavior(f"Claude Code backend did not return structured output\nresult_message_json={result_messages_json}")

                if result_message.result is None:
                    raise UnexpectedModelBehavior("Claude Code backend did not return text output")
                output_text = result_message.result
            else:
                output_text = json.dumps(structured_output, ensure_ascii=False)

            return ModelResponse(
                parts=[TextPart(content=output_text)],
                model_name=assistant_model_name,
                timestamp=_normalize_timestamp(getattr(result_message, "timestamp", None)),
                usage=_normalize_claude_code_sdk_usage_to_request_usage(getattr(result_message, "usage", None)),
            )
        finally:
            if projected_request is not None:
                projected_request.cleanup()
