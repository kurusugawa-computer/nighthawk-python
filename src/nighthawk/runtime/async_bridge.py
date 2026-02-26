from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading
from typing import Any, Awaitable, Callable, Coroutine, cast


def run_coroutine_synchronously(coroutine_call: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine_call())

    execution_context = contextvars.copy_context()
    result_container: dict[str, Any] = {}
    exception_container: dict[str, BaseException] = {}

    def run_coroutine_call_in_thread() -> None:
        try:
            result_container["result"] = execution_context.run(lambda: asyncio.run(coroutine_call()))
        except BaseException as exception:
            exception_container["exception"] = exception

    thread = threading.Thread(
        target=run_coroutine_call_in_thread,
        name="nighthawk-sync-bridge",
    )
    thread.start()
    thread.join()

    exception = exception_container.get("exception")
    if exception is not None:
        raise exception

    return result_container["result"]


def run_awaitable_value_synchronously(value: object) -> object:
    if not inspect.isawaitable(value):
        return value

    typed_awaitable_value = cast(Awaitable[Any], value)

    async def run_awaitable() -> Any:
        return await typed_awaitable_value

    return run_coroutine_synchronously(lambda: cast(Coroutine[Any, Any, Any], run_awaitable()))
