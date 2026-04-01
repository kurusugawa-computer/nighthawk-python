from __future__ import annotations

import inspect
import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from functools import update_wrapper
from typing import Any, cast

from opentelemetry.trace import get_current_span

_logger = logging.getLogger("nighthawk.resilience")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, reset_timeout: float, time_remaining: float) -> None:
        self.reset_timeout = reset_timeout
        self.time_remaining = time_remaining
        super().__init__(f"Circuit breaker is open. Resets in {time_remaining:.1f}s.")


class _CircuitBreakerState:
    """Thread-safe circuit breaker state machine."""

    def __init__(
        self,
        *,
        fail_threshold: int,
        reset_timeout: float,
        on: type[BaseException] | tuple[type[BaseException], ...],
    ) -> None:
        self._fail_threshold = fail_threshold
        self._reset_timeout = reset_timeout
        self._on = on
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._effective_state()

    def _effective_state(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._reset_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    def before_call(self) -> None:
        """Check if call is allowed. Raises :class:`CircuitOpenError` if not."""
        with self._lock:
            effective = self._effective_state()
            if effective == CircuitState.OPEN:
                assert self._last_failure_time is not None
                remaining = self._reset_timeout - (time.monotonic() - self._last_failure_time)
                raise CircuitOpenError(self._reset_timeout, max(0.0, remaining))
            # HALF_OPEN or CLOSED: allow the call.
            if effective == CircuitState.HALF_OPEN:
                self._state = CircuitState.HALF_OPEN

    def after_success(self) -> None:
        """Record a successful call. Resets the circuit to CLOSED."""
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def after_failure(self, exception: BaseException) -> None:
        """Record a failed call. May transition to OPEN."""
        if not isinstance(exception, self._on):
            return
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._fail_threshold:
                self._state = CircuitState.OPEN
                _logger.warning(
                    "Circuit breaker opened after %d failures",
                    self._failure_count,
                )
                get_current_span().add_event(
                    "nighthawk.resilience.circuit.opened",
                    {
                        "nighthawk.resilience.circuit.fail_threshold": self._fail_threshold,
                        "nighthawk.resilience.circuit.failure_count": self._failure_count,
                        "nighthawk.resilience.circuit.exception_type": type(exception).__name__,
                    },
                )

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._last_failure_time = None


class _CircuitBreakerWrapper[**P, R]:
    """Callable wrapper providing ``.state`` and ``.reset()`` attributes."""

    __name__: str
    __doc__: str | None
    __wrapped__: Callable[P, R]

    def __init__(
        self,
        function: Callable[P, R],
        breaker_state: _CircuitBreakerState,
    ) -> None:
        self._function = function
        self._breaker_state = breaker_state
        self._is_async = inspect.iscoroutinefunction(function)
        update_wrapper(self, function)

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._breaker_state.state

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        self._breaker_state.reset()

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        if self._is_async:
            return cast(R, self._async_call(*args, **kwargs))
        return self._sync_call(*args, **kwargs)

    def _sync_call(self, *args: P.args, **kwargs: P.kwargs) -> R:
        self._breaker_state.before_call()
        try:
            result = self._function(*args, **kwargs)
        except BaseException as exception:
            self._breaker_state.after_failure(exception)
            raise
        self._breaker_state.after_success()
        return result

    async def _async_call(self, *args: Any, **kwargs: Any) -> Any:
        self._breaker_state.before_call()
        try:
            result = await cast(Any, self._function(*args, **kwargs))
        except BaseException as exception:
            self._breaker_state.after_failure(exception)
            raise
        self._breaker_state.after_success()
        return result


def circuit_breaker(
    *,
    fail_threshold: int = 5,
    reset_timeout: float = 60.0,
    on: type[BaseException] | tuple[type[BaseException], ...] = Exception,
):
    """Create a circuit breaker transformer.

    Tracks failures and opens the circuit after *fail_threshold*
    consecutive failures. While open, calls are rejected immediately
    with :class:`CircuitOpenError`. After *reset_timeout* seconds, the
    circuit enters half-open state and allows one probe call. Success
    closes the circuit; failure reopens it.

    The returned wrapper has ``.state`` (:class:`CircuitState`) and
    ``.reset()`` attributes for inspection and manual control.

    This is a **stateful** transformer (like :func:`functools.lru_cache`).
    Applying the same ``circuit_breaker(...)`` call to multiple functions
    gives each its own independent state. Applying one
    ``breaker = circuit_breaker(...)`` decorator instance to multiple
    functions shares state across them.

    Args:
        fail_threshold: Number of consecutive failures before opening.
        reset_timeout: Seconds to wait before transitioning to half-open.
        on: Exception type(s) that count as failures. Defaults to
            :class:`Exception`.

    Returns:
        A decorator that wraps a function with circuit breaker logic.

    Example::

        @circuit_breaker(fail_threshold=3, reset_timeout=30)
        def call_api(request):
            ...

        call_api.state       # CircuitState.CLOSED
        call_api.reset()     # manually reset
    """
    breaker_state = _CircuitBreakerState(
        fail_threshold=fail_threshold,
        reset_timeout=reset_timeout,
        on=on,
    )

    def decorator[**P, R](function: Callable[P, R]) -> _CircuitBreakerWrapper[P, R]:
        return _CircuitBreakerWrapper(function, breaker_state)

    return decorator
