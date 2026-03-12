from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import IO, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from ..tools.registry import get_visible_tools
from .base import BackendModelBase
from .mcp_server import mcp_server_if_needed

type PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]

type SettingSource = Literal["user", "project", "local"]


class ClaudeCodeCliModelSettings(BaseModel):
    """Settings for the Claude Code CLI backend.

    Attributes:
        allowed_tool_names: Nighthawk tool names exposed to the model.
        claude_executable: Path or name of the Claude Code CLI executable.
        claude_max_turns: Maximum conversation turns.
        max_budget_usd: Maximum dollar amount to spend on API calls.
        permission_mode: Claude Code permission mode.
        setting_sources: Configuration sources to load.
        working_directory: Absolute path to the working directory for Claude Code CLI.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_tool_names: tuple[str, ...] | None = None
    claude_executable: str = "claude"
    claude_max_turns: int | None = None
    max_budget_usd: float | None = None
    permission_mode: PermissionMode | None = None
    setting_sources: list[SettingSource] | None = None
    working_directory: str = ""

    @field_validator("claude_executable")
    @classmethod
    def _validate_claude_executable(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("claude_executable must be a non-empty string")
        return value

    @field_validator("claude_max_turns")
    @classmethod
    def _validate_claude_max_turns(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("claude_max_turns must be greater than 0")
        return value

    @field_validator("max_budget_usd")
    @classmethod
    def _validate_max_budget_usd(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("max_budget_usd must be greater than 0")
        return value

    @field_validator("working_directory")
    @classmethod
    def _validate_working_directory(cls, value: str) -> str:
        if value and not Path(value).is_absolute():
            raise ValueError("working_directory must be an absolute path")
        return value


def _get_claude_code_cli_model_settings(model_settings: ModelSettings | None) -> ClaudeCodeCliModelSettings:
    if model_settings is None:
        return ClaudeCodeCliModelSettings()
    try:
        return ClaudeCodeCliModelSettings.model_validate(model_settings)
    except Exception as exception:
        raise UserError(str(exception)) from exception


def _build_mcp_configuration_file(mcp_server_url: str) -> IO[str]:
    configuration = {
        "mcpServers": {
            "nighthawk": {
                "type": "http",
                "url": mcp_server_url,
            }
        }
    }
    temporary_file = tempfile.NamedTemporaryFile(mode="wt", encoding="utf-8", prefix="nighthawk-claude-mcp-", suffix=".json")  # noqa: SIM115
    temporary_file.write(json.dumps(configuration))
    temporary_file.flush()
    return temporary_file


class _ClaudeCodeCliTurnOutcome(TypedDict):
    output_text: str
    model_name: str | None
    usage: RequestUsage


def _parse_claude_code_json_output(stdout_text: str) -> _ClaudeCodeCliTurnOutcome:
    try:
        output = json.loads(stdout_text)
    except Exception as exception:
        raise UnexpectedModelBehavior("Claude Code CLI produced invalid JSON output") from exception

    if not isinstance(output, dict):
        raise UnexpectedModelBehavior("Claude Code CLI produced non-object JSON output")

    is_error = output.get("is_error")
    if is_error:
        error_result = output.get("result", "Claude Code CLI reported an error")
        raise UnexpectedModelBehavior(f"Claude Code CLI error: {error_result}")

    structured_output = output.get("structured_output")
    if isinstance(structured_output, dict):
        result_text = json.dumps(structured_output, ensure_ascii=False)
    else:
        result_text = output.get("result")
        if not isinstance(result_text, str):
            raise UnexpectedModelBehavior("Claude Code CLI did not produce a result string")

    usage = RequestUsage()
    usage_value = output.get("usage")
    if isinstance(usage_value, dict):
        input_tokens = usage_value.get("input_tokens")
        if isinstance(input_tokens, int):
            usage.input_tokens = input_tokens

        output_tokens = usage_value.get("output_tokens")
        if isinstance(output_tokens, int):
            usage.output_tokens = output_tokens

        cache_read_input_tokens = usage_value.get("cache_read_input_tokens")
        if isinstance(cache_read_input_tokens, int):
            usage.cache_read_tokens = cache_read_input_tokens

        cache_creation_input_tokens = usage_value.get("cache_creation_input_tokens")
        if isinstance(cache_creation_input_tokens, int):
            usage.cache_write_tokens = cache_creation_input_tokens

    model_name: str | None = None
    model_usage = output.get("modelUsage")
    if isinstance(model_usage, dict):
        model_names = list(model_usage.keys())
        if model_names:
            model_name = model_names[0]

    return {
        "output_text": result_text,
        "model_name": model_name,
        "usage": usage,
    }


class ClaudeCodeCliModel(BackendModelBase):
    """Pydantic AI model that delegates to Claude Code via the CLI."""

    def __init__(self, *, model_name: str | None = None) -> None:
        super().__init__(
            backend_label="Claude Code CLI backend",
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
        return f"claude-code-cli:{self._model_name or 'default'}"

    @property
    def system(self) -> str:
        return "anthropic"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        system_prompt_file: IO[str] | None = None
        mcp_configuration_file: IO[str] | None = None

        try:
            model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)

            _, system_prompt_text, user_prompt_text = self._prepare_common_request_parts(
                messages=messages,
                model_request_parameters=model_request_parameters,
            )

            claude_code_cli_model_settings = _get_claude_code_cli_model_settings(model_settings)

            tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names = await self._prepare_allowed_tools(
                model_request_parameters=model_request_parameters,
                configured_allowed_tool_names=claude_code_cli_model_settings.allowed_tool_names,
                visible_tools=get_visible_tools(),
            )

            if allowed_tool_names:
                system_prompt_text = "\n".join(
                    [
                        system_prompt_text,
                        "",
                        "Tool access:",
                        "- Nighthawk tools are exposed via MCP; tool names are prefixed with: mcp__nighthawk__",
                        "- Example: to call nh_exec(...), use: mcp__nighthawk__nh_exec",
                    ]
                )

            output_object = model_request_parameters.output_object

            async with mcp_server_if_needed(
                tool_name_to_tool_definition=tool_name_to_tool_definition,
                tool_name_to_handler=tool_name_to_handler,
            ) as mcp_server_url:
                # Write system prompt to a temporary file to avoid CLI argument length limits.
                system_prompt_file = tempfile.NamedTemporaryFile(mode="wt", encoding="utf-8", prefix="nighthawk-claude-system-", suffix=".txt")  # noqa: SIM115
                system_prompt_file.write(system_prompt_text)
                system_prompt_file.flush()

                claude_arguments: list[str] = [
                    claude_code_cli_model_settings.claude_executable,
                    "-p",
                    "--output-format",
                    "json",
                    "--no-session-persistence",
                ]

                if self._model_name is not None:
                    claude_arguments.extend(["--model", self._model_name])

                claude_arguments.extend(["--append-system-prompt-file", system_prompt_file.name])

                permission_mode = claude_code_cli_model_settings.permission_mode
                if permission_mode == "bypassPermissions":
                    claude_arguments.append("--dangerously-skip-permissions")
                elif permission_mode is not None:
                    claude_arguments.extend(["--permission-mode", permission_mode])

                setting_sources = claude_code_cli_model_settings.setting_sources
                if setting_sources is not None:
                    claude_arguments.extend(["--setting-sources", ",".join(setting_sources)])

                claude_max_turns = claude_code_cli_model_settings.claude_max_turns
                if claude_max_turns is not None:
                    claude_arguments.extend(["--max-turns", str(claude_max_turns)])

                max_budget_usd = claude_code_cli_model_settings.max_budget_usd
                if max_budget_usd is not None:
                    claude_arguments.extend(["--max-budget-usd", str(max_budget_usd)])

                if mcp_server_url is not None:
                    mcp_configuration_file = _build_mcp_configuration_file(mcp_server_url)
                    claude_arguments.extend(["--mcp-config", mcp_configuration_file.name])

                    allowed_tool_patterns = [f"mcp__nighthawk__{tool_name}" for tool_name in allowed_tool_names]
                    for pattern in allowed_tool_patterns:
                        claude_arguments.extend(["--allowedTools", pattern])

                if output_object is not None:
                    schema = dict(output_object.json_schema)
                    if output_object.name:
                        schema["title"] = output_object.name
                    if output_object.description:
                        schema["description"] = output_object.description
                    claude_arguments.extend(["--json-schema", json.dumps(schema)])

                working_directory = claude_code_cli_model_settings.working_directory
                cwd: str | None = working_directory if working_directory else None

                # Build subprocess environment: inherit current environment but remove CLAUDECODE
                # to avoid nested-session detection. Unlike the SDK backend, this does not modify
                # the process-global environment.
                subprocess_environment = {key: value for key, value in os.environ.items() if key != "CLAUDECODE"}

                process = await asyncio.create_subprocess_exec(
                    *claude_arguments,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=subprocess_environment,
                )
                if process.stdin is None or process.stdout is None or process.stderr is None:
                    raise UnexpectedModelBehavior("Claude Code CLI subprocess streams are unexpectedly None")

                stdout_bytes, stderr_bytes = await process.communicate(input=user_prompt_text.encode("utf-8"))

                return_code = process.returncode

                if return_code != 0:
                    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                    stdout_tail = stdout_bytes.decode("utf-8", errors="replace").strip()

                    detail_parts: list[str] = []
                    if stderr_text:
                        detail_parts.append(f"stderr={stderr_text[:2000]}")
                    if stdout_tail:
                        detail_parts.append(f"stdout_tail={stdout_tail[:4000]}")
                    if not detail_parts:
                        detail_parts.append("no stderr or stdout was captured")

                    detail = " | ".join(detail_parts)
                    raise UnexpectedModelBehavior(f"Claude Code CLI exited with non-zero status. {detail}")

                stdout_text = stdout_bytes.decode("utf-8")
                turn_outcome = _parse_claude_code_json_output(stdout_text)

                return ModelResponse(
                    parts=[TextPart(content=turn_outcome["output_text"])],
                    usage=turn_outcome["usage"],
                    model_name=turn_outcome["model_name"],
                    provider_name="claude-code-cli",
                )
        except (UserError, UnexpectedModelBehavior, ValueError):
            raise
        except Exception as exception:
            raise UnexpectedModelBehavior("Claude Code CLI backend failed") from exception
        finally:
            if system_prompt_file is not None:
                with contextlib.suppress(Exception):
                    system_prompt_file.close()
            if mcp_configuration_file is not None:
                with contextlib.suppress(Exception):
                    mcp_configuration_file.close()
