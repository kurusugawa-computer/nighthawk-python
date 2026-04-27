"""Embedded MCP tool server for provider backends.

Starts a Streamable HTTP MCP server in a background thread, exposing
Nighthawk tools to CLI-based backends (e.g. Codex) that consume tools
via an MCP endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import copy_context
from typing import Any, cast

from opentelemetry import context as otel_context
from pydantic_ai import RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.tools import ToolDefinition

from ..errors import NighthawkError
from ..runtime.step_context import (
    StepContext,
    ToolResultRenderingPolicy,
    resolve_tool_result_rendering_policy,
)
from ..tools.contracts import ToolHandlerResult, ToolOutcome, build_tool_result_observation
from .mcp_boundary import call_tool_for_low_level_mcp_server, tool_handler_result_to_low_level_mcp_content


class McpServer:
    """In-process MCP tool server backed by Nighthawk tool handlers."""

    def __init__(
        self,
        *,
        tool_name_to_tool_definition: dict[str, ToolDefinition],
        tool_name_to_handler: dict[str, Callable[[dict[str, Any]], Awaitable[ToolHandlerResult]]],
        tool_result_rendering_policy: ToolResultRenderingPolicy,
        parent_otel_context: Any,
    ) -> None:
        self._tool_name_to_tool_definition = tool_name_to_tool_definition
        self._tool_name_to_handler = tool_name_to_handler
        self._tool_result_rendering_policy = tool_result_rendering_policy
        self._parent_otel_context = parent_otel_context

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
        from mcp.server.streamable_http_manager import (
            StreamableHTTPSessionManager,
        )
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
            handler = self._tool_name_to_handler.get(name)
            if handler is None:
                tool_outcome: ToolOutcome = {
                    "payload": None,
                    "error": {"kind": "resolution", "message": f"Unknown tool: {name}", "guidance": "Choose a visible tool name and retry."},
                }
                tool_handler_result: ToolHandlerResult = build_tool_result_observation(tool_outcome=tool_outcome)
                return tool_handler_result_to_low_level_mcp_content(
                    tool_name=name,
                    tool_handler_result=tool_handler_result,
                    rendering_policy=self._tool_result_rendering_policy,
                )

            return await call_tool_for_low_level_mcp_server(
                tool_name=name,
                arguments=arguments,
                tool_handler=handler,
                parent_otel_context=self._parent_otel_context,
                rendering_policy=self._tool_result_rendering_policy,
            )

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

        context = copy_context()
        server_thread = threading.Thread(
            target=context.run,
            args=(run_server,),
            name="nighthawk-mcp-server",
            daemon=True,
        )
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
            with contextlib.suppress(Exception):
                self._listening_socket.close()

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


@asynccontextmanager
async def mcp_server_if_needed(
    *,
    tool_name_to_tool_definition: dict[str, ToolDefinition],
    tool_name_to_handler: dict[str, Callable[[dict[str, Any]], Awaitable[ToolHandlerResult]]],
) -> AsyncIterator[str | None]:
    if not tool_name_to_handler:
        yield None
        return

    from pydantic_ai._run_context import get_current_run_context

    parent_otel_context = otel_context.get_current()
    parent_run_context = get_current_run_context()
    if parent_run_context is None:
        raise NighthawkError("Codex MCP tool server requires an active RunContext")
    typed_parent_run_context = cast(RunContext[StepContext], parent_run_context)
    if not isinstance(typed_parent_run_context.deps, StepContext):
        raise UnexpectedModelBehavior("Codex MCP tool server requires StepContext dependencies")
    tool_result_rendering_policy = resolve_tool_result_rendering_policy(typed_parent_run_context.deps.tool_result_rendering_policy)

    server = McpServer(
        tool_name_to_tool_definition=tool_name_to_tool_definition,
        tool_name_to_handler=tool_name_to_handler,
        tool_result_rendering_policy=tool_result_rendering_policy,
        parent_otel_context=parent_otel_context,
    )
    server.start()

    try:
        url = server.url
        port = int(url.split(":")[2].split("/", 1)[0])
        await _wait_for_tcp_listen("127.0.0.1", port, timeout_seconds=2.0)
        yield url
    finally:
        await server.stop()
