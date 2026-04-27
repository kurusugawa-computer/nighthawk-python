from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from dataclasses import replace
from typing import IO, Any, Literal, TypedDict

from pydantic import field_validator
from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import InlineDefsJsonSchemaTransformer, ModelProfile
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from ..tools.registry import get_visible_tools
from .base import BackendModelBase, BackendModelSettings, append_text_projected_tool_result_preview_prompt
from .mcp_server import mcp_server_if_needed
from .text_projection import TextProjectedRequest, resolve_text_projection_staging_root_directory

type SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
type ModelReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]


class _CodexJsonSchemaTransformer(OpenAIJsonSchemaTransformer):
    def __init__(self, schema: dict[str, Any], *, strict: bool | None = None):
        schema = InlineDefsJsonSchemaTransformer(schema, strict=strict).walk()
        super().__init__(schema, strict=strict)

    def transform(self, schema: dict[str, Any]) -> dict[str, Any]:
        if not schema:
            schema = {"type": "object"}
        elif "properties" in schema and "type" not in schema:
            schema = dict(schema)
            schema["type"] = "object"
        return super().transform(schema)


class CodexModelSettings(BackendModelSettings):
    """Settings for the Codex backend.

    Attributes:
        executable: Path or name of the Codex CLI executable.
        model_reasoning_effort: Reasoning effort level for the model.
        sandbox_mode: Codex sandbox isolation mode.
    """

    executable: str = "codex"
    model_reasoning_effort: ModelReasoningEffort | None = None
    sandbox_mode: SandboxMode | None = None

    @field_validator("executable")
    @classmethod
    def _validate_executable(cls, value: str) -> str:
        if value.strip() == "":
            raise ValueError("executable must be a non-empty string")
        return value


class _CodexTurnOutcome(TypedDict):
    output_text: str
    thread_id: str | None
    usage: RequestUsage


def _render_toml_value_text(value: object) -> str:
    # Codex CLI accepts `--config key=value` where values are TOML literals.
    # Using JSON serialization for strings/arrays produces TOML-compatible literals for the cases we need here.
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, dict):
        return json.dumps(value)
    raise TypeError(f"Unsupported config value type: {type(value).__name__}")


def _build_codex_config_arguments(configuration_overrides: dict[str, object]) -> list[str]:
    arguments: list[str] = []
    for key, value in configuration_overrides.items():
        arguments.extend(["--config", f"{key}={_render_toml_value_text(value)}"])
    return arguments


def _parse_codex_jsonl_lines(jsonl_lines: list[str]) -> _CodexTurnOutcome:
    output_text: str | None = None
    thread_id: str | None = None
    most_recent_stream_error_message: str | None = None

    usage = RequestUsage()

    for line in jsonl_lines:
        try:
            event = json.loads(line)
        except Exception as exception:
            raise UnexpectedModelBehavior("Codex CLI produced invalid JSONL output") from exception

        if not isinstance(event, dict) or "type" not in event:
            raise UnexpectedModelBehavior("Codex CLI produced an unexpected event")

        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id_value = event.get("thread_id")
            if isinstance(thread_id_value, str):
                thread_id = thread_id_value
        elif event_type == "turn.completed":
            usage_value = event.get("usage")
            if isinstance(usage_value, dict):
                input_tokens = usage_value.get("input_tokens")
                cached_input_tokens = usage_value.get("cached_input_tokens")
                output_tokens = usage_value.get("output_tokens")

                if isinstance(input_tokens, int):
                    usage.input_tokens = input_tokens
                if isinstance(cached_input_tokens, int):
                    usage.cache_read_tokens = cached_input_tokens
                if isinstance(output_tokens, int):
                    usage.output_tokens = output_tokens
        elif event_type == "turn.failed":
            error_value = event.get("error")
            if isinstance(error_value, dict) and isinstance(error_value.get("message"), str):
                raise UnexpectedModelBehavior(str(error_value.get("message")))
            raise UnexpectedModelBehavior("Codex CLI reported a failed turn")
        elif event_type == "error":
            # Codex CLI can emit transient reconnect progress as `error` events.
            # Preserve the latest message and only fail if no usable response is produced.
            message_value = event.get("message")
            most_recent_stream_error_message = message_value if isinstance(message_value, str) else "Codex CLI reported a stream error"
        elif event_type == "item.completed":
            item_value = event.get("item")
            if isinstance(item_value, dict) and item_value.get("type") == "agent_message":
                text_value = item_value.get("text")
                if isinstance(text_value, str):
                    output_text = text_value

    if output_text is None:
        if most_recent_stream_error_message is not None:
            raise UnexpectedModelBehavior(most_recent_stream_error_message)
        raise UnexpectedModelBehavior("Codex CLI did not produce an agent message")

    return {
        "output_text": output_text,
        "thread_id": thread_id,
        "usage": usage,
    }


class CodexModel(BackendModelBase):
    """Pydantic AI model that delegates to the Codex CLI."""

    def __init__(self, *, model_name: str | None = None) -> None:
        super().__init__(
            backend_label="Codex backend",
            profile=ModelProfile(
                supports_tools=True,
                supports_json_schema_output=True,
                supports_json_object_output=False,
                supports_image_output=False,
                default_structured_output_mode="native",
                supported_builtin_tools=frozenset([AbstractBuiltinTool]),
                json_schema_transformer=_CodexJsonSchemaTransformer,
            ),
        )
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return f"codex:{self._model_name or 'default'}"

    @property
    def system(self) -> str:
        return "openai"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        if model_request_parameters.output_object is not None:
            model_request_parameters = replace(
                model_request_parameters,
                output_object=replace(model_request_parameters.output_object, strict=True),
            )
        model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)

        output_schema_file: IO[str] | None = None
        projected_request: TextProjectedRequest | None = None

        try:
            codex_model_settings = CodexModelSettings.from_model_settings(model_settings)
            staging_root_directory = resolve_text_projection_staging_root_directory(
                working_directory=codex_model_settings.working_directory,
            )
            prepared_projected_request = self._prepare_text_projected_request(
                messages=messages,
                model_request_parameters=model_request_parameters,
                staging_root_directory=staging_root_directory,
                empty_prompt_exception_factory=UserError,
            )
            projected_request = prepared_projected_request.projected_request
            user_prompt_text = prepared_projected_request.user_prompt_text
            system_prompt_text = prepared_projected_request.system_prompt_text

            tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names = await self._prepare_allowed_tools(
                model_request_parameters=model_request_parameters,
                configured_allowed_tool_names=codex_model_settings.allowed_tool_names,
                visible_tools=get_visible_tools(),
            )

            if allowed_tool_names:
                system_prompt_text = append_text_projected_tool_result_preview_prompt(system_prompt_text=system_prompt_text)

            prompt_parts = [p for p in [system_prompt_text, user_prompt_text] if p]
            prompt_text = "\n\n".join(prompt_parts)

            output_object = model_request_parameters.output_object
            if output_object is None:
                output_schema_file = None
            else:
                output_schema_file = tempfile.NamedTemporaryFile(mode="wt", encoding="utf-8", prefix="nighthawk-codex-output-schema-", suffix=".json")  # noqa: SIM115
                output_schema_file.write(json.dumps(dict(output_object.json_schema)))
                output_schema_file.flush()
            async with mcp_server_if_needed(
                tool_name_to_tool_definition=tool_name_to_tool_definition,
                tool_name_to_handler=tool_name_to_handler,
            ) as mcp_server_url:
                configuration_overrides: dict[str, object] = {}

                if self._model_name is not None:
                    configuration_overrides["model"] = self._model_name

                if mcp_server_url is not None:
                    configuration_overrides["mcp_servers.nighthawk.url"] = mcp_server_url
                    configuration_overrides["mcp_servers.nighthawk.enabled_tools"] = list(allowed_tool_names)
                model_reasoning_effort = codex_model_settings.model_reasoning_effort
                if model_reasoning_effort is not None:
                    configuration_overrides["model_reasoning_effort"] = model_reasoning_effort

                codex_arguments = [
                    codex_model_settings.executable,
                    "exec",
                    "--experimental-json",
                    "--skip-git-repo-check",
                ]
                sandbox_mode = codex_model_settings.sandbox_mode
                if sandbox_mode is not None:
                    codex_arguments.extend(["--sandbox", sandbox_mode])
                codex_arguments.extend(_build_codex_config_arguments(configuration_overrides))

                if output_schema_file is not None:
                    codex_arguments.extend(["--output-schema", output_schema_file.name])

                working_directory = codex_model_settings.working_directory
                if working_directory:
                    codex_arguments.extend(["--cd", working_directory])

                process = await asyncio.create_subprocess_exec(
                    *codex_arguments,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                if process.stdin is None or process.stdout is None or process.stderr is None:
                    raise UnexpectedModelBehavior("Codex CLI subprocess streams are unexpectedly None")

                process.stdin.write(prompt_text.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()

                jsonl_lines: list[str] = []

                process_stderr = process.stderr

                async def read_stderr() -> bytes:
                    if process_stderr is None:
                        return b""
                    return await process_stderr.read()

                stderr_task = asyncio.create_task(read_stderr())

                async for line_bytes in process.stdout:
                    line_text = line_bytes.decode("utf-8").rstrip("\n")
                    if line_text:
                        jsonl_lines.append(line_text)

                return_code = await process.wait()
                stderr_bytes = await stderr_task

                if return_code != 0:
                    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                    detail_parts: list[str] = []

                    if stderr_text:
                        detail_parts.append(f"stderr={stderr_text[:2000]}")

                    recent_jsonl_lines = jsonl_lines[-8:]
                    if recent_jsonl_lines:
                        recent_jsonl_text = "\n".join(recent_jsonl_lines)
                        detail_parts.append(f"recent_jsonl_events={recent_jsonl_text[:4000]}")

                    if not detail_parts:
                        detail_parts.append("no stderr or JSONL events were captured")

                    detail = " | ".join(detail_parts)
                    raise UnexpectedModelBehavior(f"Codex CLI exited with non-zero status. {detail}")

                turn_outcome = _parse_codex_jsonl_lines(jsonl_lines)

                output_text = turn_outcome["output_text"]

                provider_details: dict[str, Any] = {
                    "codex": {
                        "thread_id": turn_outcome["thread_id"],
                    }
                }

                return ModelResponse(
                    parts=[TextPart(content=output_text)],
                    usage=turn_outcome["usage"],
                    model_name=self.model_name,
                    provider_name="codex",
                    provider_details=provider_details,
                )
        except (UserError, UnexpectedModelBehavior, ValueError):
            raise
        except Exception as exception:
            raise UnexpectedModelBehavior("Codex backend failed") from exception
        finally:
            if output_schema_file is not None:
                with contextlib.suppress(Exception):
                    output_schema_file.close()
            if projected_request is not None:
                projected_request.cleanup()
