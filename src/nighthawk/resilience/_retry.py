from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from functools import wraps
from typing import Any, cast

from tenacity import AsyncRetrying, RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from nighthawk.errors import ExecutionError

type ExceptionTypeOrTuple = type[BaseException] | tuple[type[BaseException], ...]

_logger = logging.getLogger("nighthawk")


def _default_before_sleep(retry_state: RetryCallState) -> None:
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    _logger.info(
        "Retry attempt %d for %s after %s: %s",
        retry_state.attempt_number,
        getattr(retry_state.fn, "__name__", repr(retry_state.fn)),
        type(exception).__name__ if exception else "unknown",
        exception,
    )


def _build_retrying(
    *,
    attempts: int,
    on: ExceptionTypeOrTuple,
    wait: Any,
    before_sleep: Callable[[RetryCallState], None] | None,
) -> Retrying:
    return Retrying(
        stop=stop_after_attempt(attempts),
        retry=retry_if_exception_type(on),
        wait=wait,
        reraise=True,
        before_sleep=before_sleep if before_sleep is not None else _default_before_sleep,
    )


def _build_async_retrying(
    *,
    attempts: int,
    on: ExceptionTypeOrTuple,
    wait: Any,
    before_sleep: Callable[[RetryCallState], None] | None,
) -> AsyncRetrying:
    return AsyncRetrying(
        stop=stop_after_attempt(attempts),
        retry=retry_if_exception_type(on),
        wait=wait,
        reraise=True,
        before_sleep=before_sleep if before_sleep is not None else _default_before_sleep,
    )


def _wrap_with_retry[**P, R](
    function: Callable[P, R],
    *,
    attempts: int,
    on: ExceptionTypeOrTuple,
    wait: Any,
    before_sleep: Callable[[RetryCallState], None] | None,
) -> Callable[P, R]:
    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            async for attempt in _build_async_retrying(attempts=attempts, on=on, wait=wait, before_sleep=before_sleep):
                with attempt:
                    return await function(*args, **kwargs)

        return cast(Callable[P, R], async_wrapper)

    @wraps(function)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        for attempt in _build_retrying(attempts=attempts, on=on, wait=wait, before_sleep=before_sleep):
            with attempt:
                return function(*args, **kwargs)

    return cast(Callable[P, R], sync_wrapper)


class _RetryingHandle:
    """Retry handle usable as decorator factory or tenacity iterator.

    Decorator form::

        resilient_function = retrying(attempts=3)(my_function)

    Iterator form (sync)::

        for attempt in retrying(attempts=3):
            with attempt:
                result = my_function()

    Async iterator form::

        async for attempt in retrying(attempts=3):
            with attempt:
                result = await my_function()
    """

    def __init__(
        self,
        *,
        attempts: int,
        on: ExceptionTypeOrTuple,
        wait: Any,
        before_sleep: Callable[[RetryCallState], None] | None,
    ) -> None:
        self._attempts = attempts
        self._on = on
        self._wait = wait
        self._before_sleep = before_sleep

    def __call__[**P, R](self, function: Callable[P, R]) -> Callable[P, R]:
        """Wrap *function* with retry logic (decorator factory form)."""
        return _wrap_with_retry(
            function,
            attempts=self._attempts,
            on=self._on,
            wait=self._wait,
            before_sleep=self._before_sleep,
        )

    def __iter__(self) -> Iterator[Any]:
        """Sync iterator form: ``for attempt in retrying(...)``."""
        return iter(
            _build_retrying(
                attempts=self._attempts,
                on=self._on,
                wait=self._wait,
                before_sleep=self._before_sleep,
            )
        )

    def __aiter__(self) -> AsyncIterator[Any]:
        """Async iterator form: ``async for attempt in retrying(...)``."""
        return _build_async_retrying(
            attempts=self._attempts,
            on=self._on,
            wait=self._wait,
            before_sleep=self._before_sleep,
        ).__aiter__()


def retrying(
    *,
    attempts: int = 3,
    on: ExceptionTypeOrTuple = ExecutionError,
    wait: Any | None = None,
    before_sleep: Callable[[RetryCallState], None] | None = None,
) -> _RetryingHandle:
    """Create a retry transformer.

    Can be used as a decorator factory or in a tenacity iterator pattern::

        # Decorator form
        resilient_function = retrying(attempts=3)(my_function)
        result = resilient_function(x)

        # Iterator form (sync)
        for attempt in retrying(attempts=3):
            with attempt:
                result = my_function(x)

        # Async iterator form
        async for attempt in retrying(attempts=3):
            with attempt:
                result = await my_function(x)

    Args:
        attempts: Maximum number of attempts (including the initial call).
        on: Exception type(s) that trigger a retry. Defaults to
            :class:`~nighthawk.ExecutionError`.
        wait: Tenacity wait strategy. Defaults to
            ``wait_exponential_jitter()``.
        before_sleep: Callback invoked before each retry sleep. Receives a
            tenacity ``RetryCallState``. Defaults to logging at INFO level
            on the ``nighthawk`` logger.

    Returns:
        A handle that can wrap a function or be used as an iterator.
    """
    effective_wait = wait if wait is not None else wait_exponential_jitter()
    return _RetryingHandle(
        attempts=attempts,
        on=on,
        wait=effective_wait,
        before_sleep=before_sleep,
    )
