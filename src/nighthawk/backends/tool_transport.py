"""Keep transported Python tool calls on their owning loop and preserve failures."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, copy_context
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .tool_bridge import ToolHandler


class ToolTransport:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.failure: asyncio.Future[BaseException] = self.loop.create_future()
        self.lock = asyncio.Lock()
        self.closed = False
        self.tasks: set[asyncio.Task[Any]] = set()

    def wrap(self, handler: ToolHandler) -> ToolHandler:
        context = copy_context()

        async def invoke(argument_name_to_value: dict[str, Any]) -> Any:
            async with self.lock:
                if self.failure.done():
                    raise self.failure.result()
                if self.closed:
                    raise RuntimeError("The tool transport request has finished")
                try:
                    return await handler(argument_name_to_value)
                except BaseException as exception:
                    if not self.failure.done():
                        self.failure.set_result(exception)
                    raise

        async def execute_on_owner(argument_name_to_value: dict[str, Any]) -> Any:
            task = asyncio.create_task(invoke(argument_name_to_value), context=context.copy())
            self.tasks.add(task)
            try:
                return await task
            finally:
                self.tasks.discard(task)

        async def dispatch(argument_name_to_value: dict[str, Any]) -> Any:
            if self.closed:
                if self.failure.done():
                    raise self.failure.result()
                raise RuntimeError("The tool transport request has finished")
            if asyncio.get_running_loop() is self.loop:
                return await execute_on_owner(argument_name_to_value)
            # MCP's HTTP server owns another event loop. Move execution, including
            # validation and async host hooks, back to the Natural caller's loop.
            future = context.copy().run(asyncio.run_coroutine_threadsafe, execute_on_owner(argument_name_to_value), self.loop)
            return await asyncio.wrap_future(future)

        return dispatch

    async def run[T](self, request: Callable[[], Awaitable[T]]) -> T:
        async def execute_request() -> T:
            return await request()

        token = _tool_transport_var.set(self)
        task = asyncio.create_task(execute_request())
        try:
            await asyncio.wait((task, self.failure), return_when=asyncio.FIRST_COMPLETED)
            if self.failure.done():
                raise self.failure.result()
            return task.result()
        finally:
            self.closed = True
            try:
                pending_tasks = (task, *self.tasks)
                for pending in pending_tasks:
                    if not pending.done():
                        pending.cancel()
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            finally:
                _tool_transport_var.reset(token)


_tool_transport_var: ContextVar[ToolTransport | None] = ContextVar("nighthawk_tool_transport", default=None)


def wrap_transported_tool(handler: ToolHandler) -> ToolHandler:
    transport = _tool_transport_var.get()
    return handler if transport is None else transport.wrap(handler)
