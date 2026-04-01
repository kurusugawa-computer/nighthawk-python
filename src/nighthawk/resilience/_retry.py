from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from functools import wraps
from typing import Any, cast

from opentelemetry.trace import get_current_span
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from nighthawk.errors import ExecutionError

type ExceptionTypeOrTuple = type[BaseException] | tuple[type[BaseException], ...]
type RetryIfFunction = Callable[[BaseException], bool]

_logger = logging.getLogger("nighthawk.resilience")


def _emit_retry_attempt_event(retry_state: RetryCallState) -> None:
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    get_current_span().add_event(
        "nighthawk.resilience.retry.attempt",
        {
            "nighthawk.resilience.retry.attempt_number": retry_state.attempt_number,
            "nighthawk.resilience.retry.function": getattr(retry_state.fn, "__name__", repr(retry_state.fn)),
            "nighthawk.resilience.retry.exception_type": type(exception).__name__ if exception else "unknown",
        },
    )


def _emit_retry_exhausted_event(retry_state: RetryCallState) -> None:
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    get_current_span().add_event(
        "nighthawk.resilience.retry.exhausted",
        {
            "nighthawk.resilience.retry.attempt_number": retry_state.attempt_number,
            "nighthawk.resilience.retry.function": getattr(retry_state.fn, "__name__", repr(retry_state.fn)),
            "nighthawk.resilience.retry.exception_type": type(exception).__name__ if exception else "unknown",
        },
    )


def _default_on_retry(retry_state: RetryCallState) -> None:
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    _logger.info(
        "Retry attempt %d for %s after %s: %s",
        retry_state.attempt_number,
        getattr(retry_state.fn, "__name__", repr(retry_state.fn)),
        type(exception).__name__ if exception else "unknown",
        exception,
    )


def _is_retryable_exception(
    exception: BaseException,
    *,
    on: ExceptionTypeOrTuple,
    retry_if: RetryIfFunction | None,
) -> bool:
    if not isinstance(exception, on):
        return False
    if retry_if is None:
        return True
    return retry_if(exception)


def _build_retry_predicate(
    *,
    on: ExceptionTypeOrTuple,
    retry_if: RetryIfFunction | None,
) -> Callable[[BaseException], bool]:
    return lambda exception: _is_retryable_exception(
        exception,
        on=on,
        retry_if=retry_if,
    )


def _build_on_retry_callback(
    *,
    on_retry: Callable[[RetryCallState], None] | None,
) -> Callable[[RetryCallState], None]:
    def callback(retry_state: RetryCallState) -> None:
        _emit_retry_attempt_event(retry_state)
        if on_retry is not None:
            on_retry(retry_state)
            return
        _default_on_retry(retry_state)

    return callback


def _build_after_callback(
    *,
    attempts: int,
    on: ExceptionTypeOrTuple,
    retry_if: RetryIfFunction | None,
) -> Callable[[RetryCallState], None]:
    def callback(retry_state: RetryCallState) -> None:
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        if exception is None:
            return
        if retry_state.attempt_number < attempts:
            return
        if not _is_retryable_exception(exception, on=on, retry_if=retry_if):
            return
        _emit_retry_exhausted_event(retry_state)

    return callback


def _build_retrying(
    *,
    attempts: int,
    on: ExceptionTypeOrTuple,
    wait: Any,
    on_retry: Callable[[RetryCallState], None] | None,
    retry_if: RetryIfFunction | None,
) -> Retrying:
    return Retrying(
        stop=stop_after_attempt(attempts),
        retry=retry_if_exception(_build_retry_predicate(on=on, retry_if=retry_if)),
        wait=wait,
        reraise=True,
        before_sleep=_build_on_retry_callback(on_retry=on_retry),
        after=_build_after_callback(
            attempts=attempts,
            on=on,
            retry_if=retry_if,
        ),
    )


def _build_async_retrying(
    *,
    attempts: int,
    on: ExceptionTypeOrTuple,
    wait: Any,
    on_retry: Callable[[RetryCallState], None] | None,
    retry_if: RetryIfFunction | None,
) -> AsyncRetrying:
    return AsyncRetrying(
        stop=stop_after_attempt(attempts),
        retry=retry_if_exception(_build_retry_predicate(on=on, retry_if=retry_if)),
        wait=wait,
        reraise=True,
        before_sleep=_build_on_retry_callback(on_retry=on_retry),
        after=_build_after_callback(
            attempts=attempts,
            on=on,
            retry_if=retry_if,
        ),
    )


def _wrap_with_retry[**P, R](
    function: Callable[P, R],
    *,
    attempts: int,
    on: ExceptionTypeOrTuple,
    wait: Any,
    on_retry: Callable[[RetryCallState], None] | None,
    retry_if: RetryIfFunction | None,
) -> Callable[P, R]:
    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            async for attempt in _build_async_retrying(
                attempts=attempts,
                on=on,
                wait=wait,
                on_retry=on_retry,
                retry_if=retry_if,
            ):
                with attempt:
                    return await function(*args, **kwargs)

        return cast(Callable[P, R], async_wrapper)

    @wraps(function)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        for attempt in _build_retrying(
            attempts=attempts,
            on=on,
            wait=wait,
            on_retry=on_retry,
            retry_if=retry_if,
        ):
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
        on_retry: Callable[[RetryCallState], None] | None,
        retry_if: RetryIfFunction | None,
    ) -> None:
        self._attempts = attempts
        self._on = on
        self._wait = wait
        self._on_retry = on_retry
        self._retry_if = retry_if

    def __call__[**P, R](self, function: Callable[P, R]) -> Callable[P, R]:
        """Wrap *function* with retry logic (decorator factory form)."""
        return _wrap_with_retry(
            function,
            attempts=self._attempts,
            on=self._on,
            wait=self._wait,
            on_retry=self._on_retry,
            retry_if=self._retry_if,
        )

    def __iter__(self) -> Iterator[Any]:
        """Sync iterator form: ``for attempt in retrying(...)``."""
        return iter(
            _build_retrying(
                attempts=self._attempts,
                on=self._on,
                wait=self._wait,
                on_retry=self._on_retry,
                retry_if=self._retry_if,
            )
        )

    def __aiter__(self) -> AsyncIterator[Any]:
        """Async iterator form: ``async for attempt in retrying(...)``."""
        return _build_async_retrying(
            attempts=self._attempts,
            on=self._on,
            wait=self._wait,
            on_retry=self._on_retry,
            retry_if=self._retry_if,
        ).__aiter__()


def retrying(
    *,
    attempts: int = 3,
    on: ExceptionTypeOrTuple = ExecutionError,
    wait: Any | None = None,
    on_retry: Callable[[RetryCallState], None] | None = None,
    retry_if: RetryIfFunction | None = None,
) -> _RetryingHandle:
    """Create a retry transformer.

    Retry decision order:
    1. ``on`` (type-level eligibility)
    2. ``retry_if`` (content-level eligibility)
    3. ``wait`` (interval strategy)
    4. ``on_retry`` (side-effect hook)

    Args:
        attempts: Maximum number of attempts (including the initial call).
        on: Exception type(s) eligible for retry checks.
        wait: Tenacity wait strategy. Defaults to ``wait_exponential_jitter()``.
        on_retry: Callback invoked when a retry is decided.
        retry_if: Optional predicate evaluated after ``on`` matching.

    Returns:
        A handle usable as a decorator factory or tenacity-style iterator.
    """
    effective_wait = wait if wait is not None else wait_exponential_jitter()
    return _RetryingHandle(
        attempts=attempts,
        on=on,
        wait=effective_wait,
        on_retry=on_retry,
        retry_if=retry_if,
    )
