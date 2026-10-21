from __future__ import annotations

import inspect
import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from functools import update_wrapper
from types import MethodType
from typing import Any, Concatenate, cast, overload

from opentelemetry.trace import get_current_span

from ..errors import NighthawkError

_logger = logging.getLogger("nighthawk.resilience")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(NighthawkError):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, reset_timeout: float, time_remaining: float, *, probe_in_progress: bool = False) -> None:
        self.reset_timeout = reset_timeout
        self.time_remaining = time_remaining
        self.probe_in_progress = probe_in_progress
        message = (
            "Circuit breaker recovery probe is in progress." if probe_in_progress else f"Circuit breaker is open. Resets in {time_remaining:.1f}s."
        )
        super().__init__(message)


class _CircuitBreakerState:
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
        self._generation = 0

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

    def before_call(self) -> int:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                raise CircuitOpenError(self._reset_timeout, 0.0, probe_in_progress=True)
            effective = self._effective_state()
            if effective == CircuitState.OPEN:
                assert self._last_failure_time is not None
                remaining = self._reset_timeout - (time.monotonic() - self._last_failure_time)
                raise CircuitOpenError(self._reset_timeout, max(0.0, remaining))
            if effective == CircuitState.HALF_OPEN:
                self._state = CircuitState.HALF_OPEN
            return self._generation

    def after_success(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._last_failure_time = None

    def after_failure(self, generation: int, exception: BaseException) -> None:
        opened_failure_count: int | None = None
        with self._lock:
            if generation != self._generation:
                return
            if not isinstance(exception, self._on):
                if self._state == CircuitState.HALF_OPEN:
                    # Cancellation and excluded errors release the probe without restarting the cooldown.
                    self._state = CircuitState.OPEN
                return
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._failure_count >= self._fail_threshold:
                self._state = CircuitState.OPEN
                # Calls admitted before this transition cannot overwrite recovery or a manual reset.
                self._generation += 1
                opened_failure_count = self._failure_count
        if opened_failure_count is not None:
            _logger.warning("Circuit breaker opened after %d failures", opened_failure_count)
            get_current_span().add_event(
                "nighthawk.resilience.circuit.opened",
                {
                    "nighthawk.resilience.circuit.fail_threshold": self._fail_threshold,
                    "nighthawk.resilience.circuit.failure_count": opened_failure_count,
                    "nighthawk.resilience.circuit.exception_type": type(exception).__name__,
                },
            )

    def reset(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._last_failure_time = None
            self._generation += 1


class _CircuitBreakerWrapper[**P, R]:
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
        update_wrapper(self, function, updated=())
        if self._is_async:
            inspect.markcoroutinefunction(self)

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> _CircuitBreakerWrapper[P, R]: ...

    @overload
    def __get__[T, **Parameters, ReturnValue](
        self: _CircuitBreakerWrapper[Concatenate[T, Parameters], ReturnValue], instance: T, owner: type | None = None
    ) -> _CircuitBreakerWrapper[Parameters, ReturnValue]: ...

    def __get__(self, instance: object, owner: type | None = None) -> Any:
        return self if instance is None else MethodType(self, instance)

    @property
    def state(self) -> CircuitState:
        return self._breaker_state.state

    def reset(self) -> None:
        self._breaker_state.reset()

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        if self._is_async:
            return cast(R, self._async_call(*args, **kwargs))
        return self._sync_call(*args, **kwargs)

    def _sync_call(self, *args: P.args, **kwargs: P.kwargs) -> R:
        generation = self._breaker_state.before_call()
        try:
            result = self._function(*args, **kwargs)
        except BaseException as exception:
            self._breaker_state.after_failure(generation, exception)
            raise
        self._breaker_state.after_success(generation)
        return result

    async def _async_call(self, *args: Any, **kwargs: Any) -> Any:
        generation = self._breaker_state.before_call()
        try:
            result = await cast(Any, self._function(*args, **kwargs))
        except BaseException as exception:
            self._breaker_state.after_failure(generation, exception)
            raise
        self._breaker_state.after_success(generation)
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

    While a recovery probe runs, other calls raise :class:`CircuitOpenError` with ``probe_in_progress=True``.
    Only exceptions matching ``on`` count as failures; an excluded exception releases the probe for another attempt without restarting the cooldown.
    Cancellation is excluded by the default ``on=Exception`` policy.
    Results from calls admitted before the circuit opened or was manually reset cannot change its subsequent state.
    Already admitted calls finish normally; opening or resetting the circuit does not cancel them.

    The returned wrapper has ``.state`` (:class:`CircuitState`) and
    ``.reset()`` attributes for inspection and manual control.

    This is a **stateful** transformer (like :func:`functools.lru_cache`).
    Calling ``circuit_breaker(...)`` separately for each function gives each
    its own independent state. Reusing the returned decorator instance for
    multiple functions shares state across them.
    Decorating an instance method shares its circuit across instances, while preserving normal method binding.

    Args:
        fail_threshold: Number of consecutive failures before opening.
        reset_timeout: Seconds to wait before transitioning to half-open.
        on: Exception type(s) that count as failures. Defaults to
            :class:`Exception`.

    Returns:
        decorator (Callable): A decorator that wraps a function with circuit breaker logic.
    """
    breaker_state = _CircuitBreakerState(
        fail_threshold=fail_threshold,
        reset_timeout=reset_timeout,
        on=on,
    )

    def decorator[**P, R](function: Callable[P, R]) -> _CircuitBreakerWrapper[P, R]:
        return _CircuitBreakerWrapper(function, breaker_state)

    return decorator
