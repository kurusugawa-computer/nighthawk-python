from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from collections.abc import Callable
from contextvars import ContextVar, copy_context
from functools import wraps
from typing import Any, cast

from opentelemetry.trace import get_current_span

_active_timeout_stack: ContextVar[tuple[tuple[_TimeoutHandle, asyncio.Timeout], ...]] = ContextVar("nighthawk_active_timeouts", default=())


def _emit_timeout_event(*, function_name: str, seconds: float, mode: str) -> None:
    get_current_span().add_event(
        "nighthawk.resilience.timeout.triggered",
        {
            "nighthawk.resilience.timeout.function": function_name,
            "nighthawk.resilience.timeout.seconds": seconds,
            "nighthawk.resilience.timeout.mode": mode,
        },
    )


def _wrap_with_timeout[**P, R](function: Callable[P, R], *, seconds: float) -> Callable[P, R]:
    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            deadline = asyncio.timeout(seconds)
            try:
                async with deadline:
                    return await function(*args, **kwargs)
            except TimeoutError:
                if deadline.expired():
                    _emit_timeout_event(function_name=function.__name__, seconds=seconds, mode="async")
                raise

        return cast(Callable[P, R], async_wrapper)

    @wraps(function)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(copy_context().run, function, *args, **kwargs)
            completed, _ = concurrent.futures.wait((future,), timeout=seconds)
            if not completed:
                _emit_timeout_event(function_name=function.__name__, seconds=seconds, mode="sync")
                raise TimeoutError(f"Function {function.__name__} timed out after {seconds} seconds") from None
            return future.result()

    return cast(Callable[P, R], sync_wrapper)


class _TimeoutHandle:
    def __init__(self, *, seconds: float) -> None:
        self._seconds = seconds

    def __call__[**P, R](self, function: Callable[P, R]) -> Callable[P, R]:
        return _wrap_with_timeout(function, seconds=self._seconds)

    async def __aenter__(self) -> None:
        deadline = asyncio.timeout(self._seconds)
        await deadline.__aenter__()
        _active_timeout_stack.set((*_active_timeout_stack.get(), (self, deadline)))

    async def __aexit__(self, exception_type: Any, exception_value: Any, traceback: Any) -> bool | None:
        stack = _active_timeout_stack.get()
        if not stack or stack[-1][0] is not self:
            raise RuntimeError("Timeout scopes must exit in reverse order of entry")
        _, deadline = stack[-1]
        _active_timeout_stack.set(stack[:-1])
        return await deadline.__aexit__(exception_type, exception_value, traceback)

    def __enter__(self) -> None:
        raise NotImplementedError(
            "Sync timeout context manager is not supported. "
            "Use the decorator form: timeout(seconds=N)(my_function). "
            "For async code, use 'async with timeout(seconds=N):'."
        )

    def __exit__(self, *_: Any) -> None:
        pass


def timeout(*, seconds: float) -> _TimeoutHandle:
    """Create a timeout transformer.

    Decorator form (sync and async)::

        timed_function = timeout(seconds=30)(my_function)
        result = timed_function(x)

    Async context manager form::

        async with timeout(seconds=30):
            await slow_operation()

    For sync functions, the function runs in a background thread via :class:`concurrent.futures.ThreadPoolExecutor`.
    Timeout does not stop that worker, and executor shutdown waits for it before propagating :class:`TimeoutError`.
    Caller context variables are copied, including the active run and host scopes.
    Worker context variable rebindings do not propagate back; referenced objects remain shared.

    For async functions, :func:`asyncio.timeout` requests cooperative cancellation in the caller's context.
    Owned Natural notification cleanup is still joined, so resistant cleanup can delay propagation indefinitely.
    Async context manager handles support nested and concurrent use with a separate deadline for each entry.
    A :class:`TimeoutError` raised by the wrapped function is preserved without a timeout event unless this wrapper's deadline expired.

    Args:
        seconds: Timeout threshold in seconds, excluding any additional waiting for cleanup or worker completion.

    Returns:
        A handle usable as decorator factory or async context manager.
    """
    return _TimeoutHandle(seconds=seconds)
