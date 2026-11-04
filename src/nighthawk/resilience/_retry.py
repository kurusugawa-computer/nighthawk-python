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
from tenacity.wait import wait_base

from nighthawk.errors import ExecutionError

type ExceptionTypeOrTuple = type[BaseException] | tuple[type[BaseException], ...]
type RetryIfFunction = Callable[[BaseException], bool]

_logger = logging.getLogger("nighthawk.resilience")

_DEFAULT_WAIT = wait_exponential_jitter()


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


def _build_after_callback(*, attempts: int) -> Callable[[RetryCallState], None]:
    def callback(retry_state: RetryCallState) -> None:
        # Tenacity invokes after only after deciding to retry; do not replay an application predicate.
        if retry_state.outcome is not None and retry_state.outcome.failed and retry_state.attempt_number >= attempts:
            _emit_retry_exhausted_event(retry_state)

    return callback


def _build_retrying[T: Retrying | AsyncRetrying](
    retrying_type: type[T],
    *,
    attempts: int,
    on: ExceptionTypeOrTuple,
    wait: wait_base,
    on_retry: Callable[[RetryCallState], None] | None,
    retry_if: RetryIfFunction | None,
) -> T:
    return retrying_type(
        stop=stop_after_attempt(attempts),
        retry=retry_if_exception(lambda exception: isinstance(exception, on) and (retry_if is None or retry_if(exception))),
        wait=wait,
        reraise=True,
        before_sleep=_build_on_retry_callback(on_retry=on_retry),
        after=_build_after_callback(attempts=attempts),
    )


class _RetryingHandle:
    def __init__(
        self,
        *,
        attempts: int,
        on: ExceptionTypeOrTuple,
        wait: wait_base,
        on_retry: Callable[[RetryCallState], None] | None,
        retry_if: RetryIfFunction | None,
    ) -> None:
        self._attempts = attempts
        self._on = on
        self._wait = wait
        self._on_retry = on_retry
        self._retry_if = retry_if

    def __call__[**P, R](self, function: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*arguments: Any, **keyword_name_to_value: Any) -> Any:
                async for attempt in self:
                    with attempt:
                        return await function(*arguments, **keyword_name_to_value)

            return cast(Callable[P, R], asynchronous)

        @wraps(function)
        def synchronous(*arguments: Any, **keyword_name_to_value: Any) -> Any:
            for attempt in self:
                with attempt:
                    return function(*arguments, **keyword_name_to_value)

        return cast(Callable[P, R], synchronous)

    def __iter__(self) -> Iterator[Any]:
        return iter(
            _build_retrying(
                Retrying,
                attempts=self._attempts,
                on=self._on,
                wait=self._wait,
                on_retry=self._on_retry,
                retry_if=self._retry_if,
            )
        )

    def __aiter__(self) -> AsyncIterator[Any]:
        return _build_retrying(
            AsyncRetrying,
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
    wait: wait_base = _DEFAULT_WAIT,
    on_retry: Callable[[RetryCallState], None] | None = None,
    retry_if: RetryIfFunction | None = None,
) -> _RetryingHandle:
    """Create a retry transformer.

    An exception is retried when it matches ``on`` and, if supplied, ``retry_if`` accepts it.

    Args:
        attempts: Maximum number of attempts (including the initial call).
        on: Exception type(s) eligible for retry.
        wait: Tenacity wait strategy between attempts.
        on_retry: Callback invoked when a retry is decided, replacing the default INFO log.
        retry_if: Optional predicate evaluated once per matching exception.

    Returns:
        A handle usable as a decorator factory or tenacity-style iterator.
    """
    return _RetryingHandle(
        attempts=attempts,
        on=on,
        wait=wait,
        on_retry=on_retry,
        retry_if=retry_if,
    )
