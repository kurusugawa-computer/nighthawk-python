from __future__ import annotations

import asyncio
import contextlib
import contextvars
import inspect
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, cast


@dataclass
class _BridgeCleanup:
    deadline: float | None = None


_bridge_cleanup: contextvars.ContextVar[_BridgeCleanup | None] = contextvars.ContextVar("nighthawk_bridge_cleanup", default=None)


def bound_interrupted_bridge_cleanup(timeout: float) -> None:
    cleanup = _bridge_cleanup.get()
    if cleanup is not None:
        deadline = time.monotonic() + timeout
        cleanup.deadline = min(deadline, cleanup.deadline) if cleanup.deadline is not None else deadline


def _close_loop_with_deadline(loop: asyncio.AbstractEventLoop, deadline: float) -> None:
    """Keep cancellation-resistant cleanup from extending an interruption.

    asyncio.run waits for every cancelled task during shutdown. An interrupted
    notification instead gets one shared budget for delivery and loop cleanup.
    Closing this private loop does not prove that pending cleanup completed.
    """
    try:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            pending.add(loop.create_task(loop.shutdown_asyncgens()))
            # wait_for(gather(...)) can itself wait forever for cancellation.
            # Cleanup must not replace the original interruption.
            with contextlib.suppress(BaseException):
                loop.run_until_complete(asyncio.wait(pending, timeout=remaining))
        for task in pending:
            if not task.done():
                task.cancel()
            elif not task.cancelled():
                task.exception()
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def run_coroutine_synchronously(coroutine_call: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    def execute_coroutine() -> Any:
        cleanup = _BridgeCleanup()
        cleanup_token = _bridge_cleanup.set(cleanup)
        runner = asyncio.Runner()
        try:
            return runner.run(coroutine_call())
        finally:
            try:
                if cleanup.deadline is None:
                    runner.close()
                else:
                    _close_loop_with_deadline(runner.get_loop(), cleanup.deadline)
            finally:
                _bridge_cleanup.reset(cleanup_token)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return execute_coroutine()

    execution_context = contextvars.copy_context()
    result_container: dict[str, Any] = {}
    exception_container: dict[str, BaseException] = {}

    def run_coroutine_call_in_thread() -> None:
        try:
            result_container["result"] = execution_context.run(execute_coroutine)
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
        raise exception.with_traceback(exception.__traceback__)

    return result_container["result"]


def run_awaitable_value_synchronously(value: object) -> object:
    if not inspect.isawaitable(value):
        return value

    typed_awaitable_value = cast(Awaitable[Any], value)

    async def _await_value() -> Any:
        return await typed_awaitable_value

    return run_coroutine_synchronously(_await_value)
