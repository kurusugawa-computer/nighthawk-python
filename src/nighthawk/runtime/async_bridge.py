from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, cast

from .interruption import chain_cleanup_errors


@dataclass
class _BridgeState:
    lock: threading.RLock = field(default_factory=threading.RLock)
    completed: threading.Event = field(default_factory=threading.Event)
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task[Any] | None = None
    cancellation_requested: bool = False
    cancellation_delivered: bool = False
    result: Any = None
    exception: BaseException | None = None

    def request_cancellation(self) -> None:
        with self.lock:
            self.cancellation_requested = True
            if self.loop is not None and self.task is not None and not self.cancellation_delivered:
                self.cancellation_delivered = True
                self.loop.call_soon_threadsafe(self.task.cancel)


async def _shutdown_loop_work() -> None:
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
    for task in pending:
        if not task.cancelling():
            task.cancel()
    failures: list[BaseException] = []
    if pending:
        outcomes = await asyncio.gather(*pending, return_exceptions=True)
        failures.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError))
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def record_shutdown_error(loop: asyncio.AbstractEventLoop, error_name_to_value: dict[str, Any]) -> None:
        exception = error_name_to_value.get("exception")
        if isinstance(exception, BaseException):
            failures.append(exception)
        elif previous_handler is not None:
            previous_handler(loop, error_name_to_value)
        else:
            loop.default_exception_handler(error_name_to_value)

    # shutdown_asyncgens reports finalizer errors to the loop instead of raising.
    # Retain those errors in the bridge outcome, alongside task/executor failures.
    loop.set_exception_handler(record_shutdown_error)
    try:
        for shutdown in (loop.shutdown_asyncgens, loop.shutdown_default_executor):
            try:
                await shutdown()
            except BaseException as exception:
                failures.append(exception)
    finally:
        loop.set_exception_handler(previous_handler)
    if failures:
        raise BaseExceptionGroup("Errors shutting down bridge-owned work", failures)


def _execute_coroutine(state: _BridgeState, coroutine_call: Callable[[], Coroutine[Any, Any, Any]]) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    primary: BaseException | None = None

    def finish(task: asyncio.Task[Any]) -> Any:
        nonlocal primary
        while not task.done():
            try:
                loop.run_until_complete(task)
            except BaseException as exception:
                if task.done():
                    raise
                if primary is None:
                    primary = exception
                    state.request_cancellation()
                # Subsequent caller interruptions do not cancel a finalizer again.
        return task.result()

    try:
        coroutine = coroutine_call()
        try:
            task = loop.create_task(coroutine)
        except BaseException:
            coroutine.close()
            raise
        with state.lock:
            state.loop, state.task = loop, task
            if state.cancellation_requested:
                state.request_cancellation()
        try:
            state.result = finish(task)
        except BaseException as exception:
            state.exception = exception
    except BaseException as exception:
        state.exception = exception
    finally:
        # Stop publishing a task before shutdown, so a concurrent cancellation
        # request cannot target a closed loop or interrupt shutdown finalizers.
        with state.lock:
            state.loop, state.task = None, None
        try:
            finish(loop.create_task(_shutdown_loop_work()))
        except BaseException as exception:
            if state.exception is None:
                state.exception = exception
            else:
                chain_cleanup_errors(state.exception, exception)
        finally:
            asyncio.set_event_loop(None)
            loop.close()
        if primary is not None:
            if state.exception is not None:
                chain_cleanup_errors(primary, state.exception)
            state.exception = primary


def run_coroutine_synchronously(coroutine_call: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run and join owned async work, cooperatively delivering caller interruption.

    Completion has no deadline: resistant cleanup must actually finish. An explicit
    completion signal, not Thread.join's platform-dependent status, proves shutdown.
    """
    state = _BridgeState()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _execute_coroutine(state, coroutine_call)
    else:
        execution_context = contextvars.copy_context()

        def worker() -> None:
            try:
                execution_context.run(_execute_coroutine, state, coroutine_call)
            except BaseException as exception:
                state.exception = exception
            finally:
                state.completed.set()

        thread = threading.Thread(target=worker, name="nighthawk-sync-bridge")
        primary: BaseException | None = None
        try:
            thread.start()
        except BaseException as exception:
            # CPython publishes the native handle before Python bootstrap sets
            # Thread.ident. start() can be interrupted while waiting for bootstrap.
            # The handle was renamed in 3.14; do not infer completion from it.
            handle = getattr(thread, "_os_thread_handle", getattr(thread, "_handle", None))
            if thread.ident is None and not getattr(handle, "ident", 0):
                raise
            primary = exception
            state.request_cancellation()
        while not state.completed.is_set():
            try:
                if thread.ident is not None:
                    thread.join(0.05)
                if not state.completed.is_set():
                    state.completed.wait(0.01)
            except BaseException as exception:
                if primary is None:
                    primary = exception
                    state.request_cancellation()
        if primary is not None:
            if state.exception is not None:
                chain_cleanup_errors(primary, state.exception)
            raise primary
    if state.exception is not None:
        raise state.exception
    return state.result


def run_awaitable_value_synchronously(value: object) -> object:
    if not inspect.isawaitable(value):
        return value

    typed_awaitable_value = cast(Awaitable[Any], value)

    async def _await_value() -> Any:
        return await typed_awaitable_value

    return run_coroutine_synchronously(_await_value)
