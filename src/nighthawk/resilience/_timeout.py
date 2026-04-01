from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from opentelemetry.trace import get_current_span


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
            try:
                async with asyncio.timeout(seconds):
                    return await function(*args, **kwargs)
            except TimeoutError:
                _emit_timeout_event(function_name=function.__name__, seconds=seconds, mode="async")
                raise

        return cast(Callable[P, R], async_wrapper)

    @wraps(function)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(function, *args, **kwargs)
            try:
                return future.result(timeout=seconds)
            except concurrent.futures.TimeoutError:
                _emit_timeout_event(function_name=function.__name__, seconds=seconds, mode="sync")
                raise TimeoutError(f"Function {function.__name__} timed out after {seconds} seconds") from None

    return cast(Callable[P, R], sync_wrapper)


class _TimeoutHandle:
    """Timeout handle usable as decorator factory or async context manager.

    Decorator form (sync and async)::

        timed_function = timeout(seconds=30)(my_function)

    Async context manager form::

        async with timeout(seconds=30):
            await slow_operation()

    The sync context manager form is not supported because Python cannot
    interrupt a running thread from the same thread. Use the decorator
    form instead.

    For sync functions wrapped via the decorator form, the function runs
    in a background thread. Note that the underlying thread continues
    running after timeout, only the caller is unblocked.
    """

    def __init__(self, *, seconds: float) -> None:
        self._seconds = seconds
        self._async_timeout: asyncio.Timeout | None = None

    def __call__[**P, R](self, function: Callable[P, R]) -> Callable[P, R]:
        """Wrap *function* with a timeout (decorator factory form)."""
        return _wrap_with_timeout(function, seconds=self._seconds)

    async def __aenter__(self) -> None:
        self._async_timeout = asyncio.timeout(self._seconds)
        await self._async_timeout.__aenter__()

    async def __aexit__(self, exception_type: Any, exception_value: Any, traceback: Any) -> bool | None:
        if self._async_timeout is not None:
            return await self._async_timeout.__aexit__(exception_type, exception_value, traceback)
        return None

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

    For sync functions, the function runs in a background thread via
    :class:`concurrent.futures.ThreadPoolExecutor`. Note that the
    underlying thread continues running after timeout, only the caller
    is unblocked with a :class:`TimeoutError`. This is a documented
    limitation of the thread-based approach, chosen for cross-platform
    compatibility.

    For async functions, uses :func:`asyncio.timeout` which provides true
    cancellation.

    Args:
        seconds: Maximum execution time in seconds.

    Returns:
        A handle usable as decorator factory or async context manager.
    """
    return _TimeoutHandle(seconds=seconds)
