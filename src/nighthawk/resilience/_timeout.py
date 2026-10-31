from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from collections.abc import Callable
from contextvars import copy_context
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


def timeout(*, seconds: float):
    """Create a timeout transformer for synchronous and asynchronous functions.

    For sync functions, the function runs in a background thread via :class:`concurrent.futures.ThreadPoolExecutor`.
    Timeout does not stop that worker, and executor shutdown waits for it before propagating :class:`TimeoutError`.
    Caller context variables are copied, including the active run and host scopes.
    Worker context variable rebindings do not propagate back; referenced objects remain shared.

    For async functions, :func:`asyncio.timeout` requests cooperative cancellation in the caller's context.
    Owned Natural notification cleanup is still joined, so resistant cleanup can delay propagation indefinitely.
    A :class:`TimeoutError` raised by the wrapped function is preserved without a timeout event unless this wrapper's deadline expired.
    Use :func:`asyncio.timeout` directly to bound a block of asynchronous code.

    Args:
        seconds: Timeout threshold in seconds, excluding any additional waiting for cleanup or worker completion.

    Returns:
        decorator (Callable): A decorator that wraps a function with the timeout.
    """

    def decorator[**P, R](function: Callable[P, R]) -> Callable[P, R]:
        return _wrap_with_timeout(function, seconds=seconds)

    return decorator
