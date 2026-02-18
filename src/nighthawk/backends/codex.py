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
from typing import Any, TypedDict

import tiktoken
from pydantic_ai.builtin_tools import AbstractBuiltinTool
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import InlineDefsJsonSchemaTransformer, ModelProfile
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from ..configuration import RunConfiguration
from ..runtime.scoping import get_environment
from ..tools.registry import get_visible_tools
from . import BackendModelBase


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
        if not isinstance(allowed_tool_names_value, (list, tuple)) or not all(isinstance(name, str) for name in allowed_tool_names_value):
            raise UserError("allowed_tool_names must be a list[str], tuple[str, ...], or None")
        allowed_tool_names = tuple(allowed_tool_names_value)

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
        run_configuration: RunConfiguration,
    ) -> None:
        self._tool_name_to_tool_definition = tool_name_to_tool_definition
        self._tool_name_to_handler = tool_name_to_handler
        self._run_configuration = run_configuration

        self._server: Any | None = None
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

        import uvicorn
        from mcp.server.fastmcp.server import StreamableHTTPASGIApp
        from mcp.server.lowlevel.server import Server as McpLowLevelServer
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Route

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

        @mcp_server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
            from mcp import types as mcp_types

            from ..tools.contracts import tool_result_failure_json_text

            handler = self._tool_name_to_handler.get(name)
            if handler is None:
                run_configuration = self._run_configuration
                result_text = tool_result_failure_json_text(
                    kind="resolution",
                    message=f"Unknown tool: {name}",
                    guidance="Choose a visible tool name and retry.",
                    max_tokens=run_configuration.context_limits.tool_result_max_tokens,
                    encoding=tiktoken.get_encoding(run_configuration.tokenizer_encoding),
                    style=run_configuration.json_renderer_style,
                )
                return [mcp_types.TextContent(type="text", text=result_text)]

            try:
                result_text = await handler(arguments)
            except Exception as exception:
                run_configuration = self._run_configuration
                result_text = tool_result_failure_json_text(
                    kind="internal",
                    message=str(exception),
                    guidance="The tool boundary wrapper failed. Retry or report this error.",
                    max_tokens=run_configuration.context_limits.tool_result_max_tokens,
                    encoding=tiktoken.get_encoding(run_configuration.tokenizer_encoding),
                    style=run_configuration.json_renderer_style,
                )
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

    run_configuration = get_environment().run_configuration

    server = _McpToolServer(
        tool_name_to_tool_definition=tool_name_to_tool_definition,
        tool_name_to_handler=tool_name_to_handler,
        run_configuration=run_configuration,
    )
    server.start()

    try:
        url = server.url
        port = int(url.split(":")[2].split("/", 1)[0])
        await _wait_for_tcp_listen("127.0.0.1", port, timeout_seconds=2.0)
        yield url
    finally:
        await server.stop()


class CodexModel(BackendModelBase):
    def __init__(self, *, model_name: str | None = None) -> None:
        super().__init__(
            backend_label="Codex CLI backend",
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

        output_schema_file: tempfile._TemporaryFileWrapper | None = None

        try:
            _most_recent_request, system_prompt_text, user_prompt_text = self._prepare_common_request_parts(
                messages=messages,
                model_request_parameters=model_request_parameters,
            )

            prompt_parts = [p for p in [system_prompt_text, user_prompt_text] if p]
            prompt_text = "\n\n".join(prompt_parts)

            codex_cli_model_settings = _get_codex_cli_model_settings(model_settings)

            tool_name_to_tool_definition, tool_name_to_handler, allowed_tool_names = await self._prepare_allowed_tools(
                model_request_parameters=model_request_parameters,
                configured_allowed_tool_names=codex_cli_model_settings.get("allowed_tool_names"),
                visible_tools=get_visible_tools(),
            )

            output_object = model_request_parameters.output_object
            if output_object is None:
                output_schema_file = None
            else:
                output_schema_file = tempfile.NamedTemporaryFile(mode="wt", encoding="utf-8", prefix="nighthawk-codex-output-schema-", suffix=".json")
                output_schema_file.write(json.dumps(dict(output_object.json_schema)))
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
