from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, TypedDict

import uvicorn
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.lowlevel.server import Server as McpLowLevelServer
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
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
from pydantic_ai.profiles import InlineDefsJsonSchemaTransformer, ModelProfile
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RequestUsage
from starlette.applications import Starlette
from starlette.routing import Route

from ..execution.environment import get_environment
from ..tools import get_visible_tools
from ..tools.assignment import serialize_value_to_json_text


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


class CodexCliModelSettings(TypedDict, total=False):
    allowed_tool_names: tuple[str, ...] | None
    codex_executable: str


def _get_codex_cli_model_settings(model_settings: ModelSettings | None) -> CodexCliModelSettings:
    default_settings: CodexCliModelSettings = {
        "allowed_tool_names": None,
        "codex_executable": "codex",
    }
    if model_settings is None:
        return default_settings

    allowed_tool_names_value = model_settings.get("allowed_tool_names")
    if allowed_tool_names_value is None:
        allowed_tool_names = None
    else:
        if not isinstance(allowed_tool_names_value, tuple) or not all(isinstance(name, str) for name in allowed_tool_names_value):
            raise UserError("allowed_tool_names must be a tuple[str, ...] or None")
        allowed_tool_names = allowed_tool_names_value

    codex_executable_value = model_settings.get("codex_executable")
    if codex_executable_value is None:
        codex_executable = "codex"
    else:
        if not isinstance(codex_executable_value, str) or codex_executable_value.strip() == "":
            raise UserError("codex_executable must be a non-empty string")
        codex_executable = codex_executable_value

    return {
        "allowed_tool_names": allowed_tool_names,
        "codex_executable": codex_executable,
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
                raise UserError("Codex CLI backend does not support non-text user prompts")
        elif isinstance(part, RetryPromptPart):
            parts.append(part.model_response())
        elif isinstance(part, ToolReturnPart):
            raise UserError("Codex CLI backend does not support tool-return parts")

    return "\n\n".join(p for p in parts if p)


class _CodexTurnOutcome(TypedDict):
    output_text: str
    thread_id: str | None
    usage: RequestUsage


def _toml_value_text(value: object) -> str:
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


def _build_codex_cli_config_arguments(configuration_overrides: dict[str, object]) -> list[str]:
    arguments: list[str] = []
    for key, value in configuration_overrides.items():
        arguments.extend(["--config", f"{key}={_toml_value_text(value)}"])
    return arguments


class _McpToolServer:
    def __init__(
        self,
        *,
        tool_name_to_tool_definition: dict[str, ToolDefinition],
        tool_name_to_handler: dict[str, Callable[[dict[str, Any]], Awaitable[str]]],
    ) -> None:
        self._tool_name_to_tool_definition = tool_name_to_tool_definition
        self._tool_name_to_handler = tool_name_to_handler

        self._server: uvicorn.Server | None = None
        self._server_thread: threading.Thread | None = None
        self._listening_socket: socket.socket | None = None
        self._url: str | None = None

    @property
    def url(self) -> str:
        if self._url is None:
            raise RuntimeError("MCP tool server is not started")
        return self._url

    def start(self) -> None:
        if self._server_thread is not None:
            raise RuntimeError("MCP tool server is already started")

        mcp_server = McpLowLevelServer("nighthawk")

        @mcp_server.list_tools()
        async def list_tools() -> list[Any]:
            from mcp import types as mcp_types

            tools: list[mcp_types.Tool] = []
            for tool_name in sorted(self._tool_name_to_handler.keys()):
                tool_definition = self._tool_name_to_tool_definition.get(tool_name)
                if tool_definition is None:
                    raise RuntimeError(f"Tool definition missing for {tool_name!r}")

                tools.append(
                    mcp_types.Tool(
                        name=tool_name,
                        description=tool_definition.description or "",
                        inputSchema=tool_definition.parameters_json_schema,
                    )
                )
            return tools

        @mcp_server.call_tool(validate_input=True)
        async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
            from mcp import types as mcp_types

            handler = self._tool_name_to_handler.get(name)
            if handler is None:
                raise ValueError(f"Unknown tool: {name}")

            result_text = await handler(arguments)
            return [mcp_types.TextContent(type="text", text=result_text)]

        session_manager = StreamableHTTPSessionManager(app=mcp_server)
        streamable_http_asgi = StreamableHTTPASGIApp(session_manager)

        starlette_application = Starlette(
            routes=[Route("/mcp", endpoint=streamable_http_asgi)],
            lifespan=lambda _app: session_manager.run(),
        )

        listening_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listening_socket.bind(("127.0.0.1", 0))
        listening_socket.listen(128)
        host, port = listening_socket.getsockname()

        configuration = uvicorn.Config(
            starlette_application,
            host=str(host),
            port=int(port),
            log_level="warning",
            lifespan="on",
        )
        server = uvicorn.Server(configuration)

        def run_server() -> None:
            server.run(sockets=[listening_socket])

        server_thread = threading.Thread(target=run_server, name="nighthawk-mcp-server", daemon=True)
        server_thread.start()

        self._server = server
        self._server_thread = server_thread
        self._listening_socket = listening_socket
        self._url = f"http://127.0.0.1:{port}/mcp"

    async def stop(self) -> None:
        if self._server is None or self._server_thread is None:
            return

        self._server.should_exit = True
        await asyncio.to_thread(self._server_thread.join, 5)

        if self._listening_socket is not None:
            try:
                self._listening_socket.close()
            except Exception:
                pass

        self._server = None
        self._server_thread = None
        self._listening_socket = None
        self._url = None


async def _wait_for_tcp_listen(host: str, port: int, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except OSError:
            await asyncio.sleep(0.01)
            continue
        else:
            writer.close()
            await writer.wait_closed()
            return
    raise UnexpectedModelBehavior("Timed out waiting for MCP tool server to start")


def _parse_codex_jsonl_lines(jsonl_lines: list[str]) -> _CodexTurnOutcome:
    output_text: str | None = None
    thread_id: str | None = None

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
            message_value = event.get("message")
            if isinstance(message_value, str):
                raise UnexpectedModelBehavior(message_value)
            raise UnexpectedModelBehavior("Codex CLI reported a fatal stream error")
        elif event_type == "item.completed":
            item_value = event.get("item")
            if isinstance(item_value, dict) and item_value.get("type") == "agent_message":
                text_value = item_value.get("text")
                if isinstance(text_value, str):
                    output_text = text_value

    if output_text is None:
        raise UnexpectedModelBehavior("Codex CLI did not produce an agent message")

    return {
        "output_text": output_text,
        "thread_id": thread_id,
        "usage": usage,
    }


@asynccontextmanager
async def _mcp_tool_server_if_needed(
    *,
    tool_name_to_tool_definition: dict[str, ToolDefinition],
    tool_name_to_handler: dict[str, Callable[[dict[str, Any]], Awaitable[str]]],
) -> AsyncIterator[str | None]:
    if not tool_name_to_handler:
        yield None
        return

    server = _McpToolServer(
        tool_name_to_tool_definition=tool_name_to_tool_definition,
        tool_name_to_handler=tool_name_to_handler,
    )
    server.start()

    try:
        url = server.url
        port = int(url.split(":")[2].split("/", 1)[0])
        await _wait_for_tcp_listen("127.0.0.1", port, timeout_seconds=2.0)
        yield url
    finally:
        await server.stop()


class CodexModel(Model):
    def __init__(self, *, model_name: str | None = None) -> None:
        super().__init__(
            profile=ModelProfile(
                supports_tools=True,
                supports_json_schema_output=True,
                supports_json_object_output=False,
                supports_image_output=False,
                default_structured_output_mode="native",
                supported_builtin_tools=frozenset([AbstractBuiltinTool]),
                json_schema_transformer=_CodexJsonSchemaTransformer,
            )
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

        output_schema_file: tempfile._TemporaryFileWrapper | None = None

        try:
            if model_request_parameters.builtin_tools:
                raise UserError("Codex CLI backend does not support builtin tools")

            if model_request_parameters.allow_image_output:
                raise UserError("Codex CLI backend does not support image output")

            most_recent_request = _find_most_recent_model_request(messages)

            system_prompt_text = _collect_system_prompt_text(most_recent_request)

            instructions = self._get_instructions(messages, model_request_parameters)
            if instructions:
                if system_prompt_text:
                    system_prompt_text = "\n\n".join([system_prompt_text, instructions])
                else:
                    system_prompt_text = instructions

            user_prompt_text = _collect_user_prompt_text(most_recent_request)
            if user_prompt_text.strip() == "":
                raise UserError("Codex CLI backend requires a non-empty user prompt")

            prompt_parts = [p for p in [system_prompt_text, user_prompt_text] if p]
            prompt_text = "\n\n".join(prompt_parts)

            codex_cli_model_settings = _get_codex_cli_model_settings(model_settings)

            tool_name_to_handler, available_tool_names = await self._build_tool_name_to_handler(model_request_parameters)
            allowed_tool_names = self._resolve_allowed_tool_names(
                model_request_parameters,
                codex_cli_model_settings=codex_cli_model_settings,
                available_tool_names=available_tool_names,
            )

            tool_name_to_tool_definition = {name: tool_definition for name, tool_definition in model_request_parameters.tool_defs.items() if name in allowed_tool_names}
            tool_name_to_handler = {name: tool_name_to_handler[name] for name in allowed_tool_names}

            output_object = model_request_parameters.output_object
            if output_object is None:
                output_json_schema = None
            else:
                output_json_schema = dict(output_object.json_schema)
                output_schema_file = tempfile.NamedTemporaryFile(mode="wt", encoding="utf-8", prefix="nighthawk-codex-output-schema-", suffix=".json")
                output_schema_file.write(json.dumps(output_json_schema))
                output_schema_file.flush()
            async with _mcp_tool_server_if_needed(
                tool_name_to_tool_definition=tool_name_to_tool_definition,
                tool_name_to_handler=tool_name_to_handler,
            ) as mcp_server_url:
                configuration_overrides: dict[str, object] = {}

                if self._model_name is not None:
                    configuration_overrides["model"] = self._model_name

                if mcp_server_url is not None:
                    configuration_overrides["mcp_servers.nighthawk.url"] = mcp_server_url
                    configuration_overrides["mcp_servers.nighthawk.enabled_tools"] = list(allowed_tool_names)

                codex_cli_arguments = [
                    codex_cli_model_settings.get("codex_executable", "codex"),
                    "exec",
                    "--json",
                    "--skip-git-repo-check",
                ]
                codex_cli_arguments.extend(_build_codex_cli_config_arguments(configuration_overrides))

                if output_schema_file is not None:
                    codex_cli_arguments.extend(["--output-schema", output_schema_file.name])

                try:
                    working_directory = get_environment().workspace_root
                except Exception:
                    working_directory = None

                if working_directory is not None:
                    codex_cli_arguments.extend(["--cd", str(working_directory)])

                process = await asyncio.create_subprocess_exec(
                    *codex_cli_arguments,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None

                process.stdin.write(prompt_text.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()

                jsonl_lines: list[str] = []

                process_stderr = process.stderr

                async def read_stderr() -> bytes:
                    assert process_stderr is not None
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
                    detail = stderr_text[:2000] if stderr_text else ""
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
            raise UnexpectedModelBehavior("Codex CLI backend failed") from exception
        finally:
            if output_schema_file is not None:
                try:
                    output_schema_file.close()
                except Exception:
                    pass

    def _resolve_allowed_tool_names(
        self,
        model_request_parameters: ModelRequestParameters,
        *,
        codex_cli_model_settings: CodexCliModelSettings,
        available_tool_names: tuple[str, ...],
    ) -> tuple[str, ...]:
        configured_allowlist = codex_cli_model_settings.get("allowed_tool_names")
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
    ) -> tuple[dict[str, Callable[[dict[str, Any]], Awaitable[str]]], tuple[str, ...]]:
        current_run_context = _get_current_run_context_or_none()
        if current_run_context is None:
            raise UnexpectedModelBehavior("Codex CLI backend requires a Pydantic AI RunContext")

        visible_tools = get_visible_tools()
        toolset = FunctionToolset(visible_tools)
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
                result = await tool.toolset.call_tool(tool_name, validated_arguments, run_context, tool)
                return serialize_value_to_json_text(result)

            tool_name_to_handler[tool_name] = handler

        return tool_name_to_handler, tuple(tool_name_to_handler.keys())


def _get_current_run_context_or_none() -> RunContext[Any] | None:
    from pydantic_ai._run_context import get_current_run_context

    return get_current_run_context()
