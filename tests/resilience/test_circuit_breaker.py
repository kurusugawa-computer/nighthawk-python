from __future__ import annotations

import asyncio
import time

import pytest

from nighthawk.resilience import CircuitOpenError, CircuitState, circuit_breaker

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class TestCircuitBreakerSync:
    def test_closed_allows_calls(self) -> None:
        @circuit_breaker(fail_threshold=3)
        def succeed() -> str:
            return "ok"

        assert succeed() == "ok"
        assert succeed.state == CircuitState.CLOSED

    def test_opens_after_threshold_failures(self) -> None:
        call_count = 0

        @circuit_breaker(fail_threshold=3, on=ValueError)
        def failing() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("boom")

        for _ in range(3):
            with pytest.raises(ValueError):
                failing()

        assert failing.state == CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            failing()
        # The function was NOT called on the 4th attempt (circuit open)
        assert call_count == 3

    def test_rejects_calls_when_open(self) -> None:
        @circuit_breaker(fail_threshold=2, reset_timeout=100, on=ValueError)
        def failing() -> str:
            raise ValueError("boom")

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                failing()

        assert failing.state == CircuitState.OPEN

        error = None
        with pytest.raises(CircuitOpenError) as exception_info:
            failing()
        error = exception_info.value
        assert error.time_remaining > 0

    def test_half_open_after_reset_timeout(self) -> None:
        @circuit_breaker(fail_threshold=2, reset_timeout=0.1, on=ValueError)
        def failing() -> str:
            raise ValueError("boom")

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                failing()

        assert failing.state == CircuitState.OPEN

        # Wait for reset timeout
        time.sleep(0.15)
        assert failing.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self) -> None:
        call_count = 0

        @circuit_breaker(fail_threshold=2, reset_timeout=0.1, on=ValueError)
        def sometimes_fail() -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ValueError("fail")
            return "ok"

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                sometimes_fail()
        assert sometimes_fail.state == CircuitState.OPEN

        # Wait for half-open
        time.sleep(0.15)

        # Probe succeeds -> closes circuit
        assert sometimes_fail() == "ok"
        assert sometimes_fail.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self) -> None:
        @circuit_breaker(fail_threshold=2, reset_timeout=0.1, on=ValueError)
        def always_fail() -> str:
            raise ValueError("fail")

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                always_fail()
        assert always_fail.state == CircuitState.OPEN

        # Wait for half-open
        time.sleep(0.15)

        # Probe fails -> reopens
        with pytest.raises(ValueError):
            always_fail()
        assert always_fail.state == CircuitState.OPEN

    def test_manual_reset(self) -> None:
        @circuit_breaker(fail_threshold=2, on=ValueError)
        def failing() -> str:
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                failing()
        assert failing.state == CircuitState.OPEN

        failing.reset()
        assert failing.state == CircuitState.CLOSED

    def test_non_matching_exception_does_not_count(self) -> None:
        call_count = 0

        @circuit_breaker(fail_threshold=2, on=ValueError)
        def raise_type_error() -> str:
            nonlocal call_count
            call_count += 1
            raise TypeError("not counted")

        for _ in range(5):
            with pytest.raises(TypeError):
                raise_type_error()

        # TypeError is not in `on`, so circuit stays closed
        assert raise_type_error.state == CircuitState.CLOSED
        assert call_count == 5

    def test_preserves_function_metadata(self) -> None:
        @circuit_breaker(fail_threshold=3)
        def my_function() -> str:
            """My doc."""
            return "ok"

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My doc."

    def test_success_resets_failure_count(self) -> None:
        call_count = 0

        @circuit_breaker(fail_threshold=3, on=ValueError)
        def intermittent() -> str:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise ValueError("fail")
            return "ok"

        # call 1: ok (count reset), call 2: fail (count=1),
        # call 3: ok (count reset), call 4: fail (count=1)
        assert intermittent() == "ok"
        with pytest.raises(ValueError):
            intermittent()
        assert intermittent() == "ok"
        with pytest.raises(ValueError):
            intermittent()

        # Circuit should still be closed (never reached threshold)
        assert intermittent.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Async
# ---------------------------------------------------------------------------


class TestCircuitBreakerAsync:
    def test_async_circuit_breaker(self) -> None:
        call_count = 0

        @circuit_breaker(fail_threshold=2, on=ValueError)
        async def async_failing() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("fail")

        for _ in range(2):
            with pytest.raises(ValueError):
                asyncio.run(async_failing())

        assert async_failing.state == CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            asyncio.run(async_failing())
        assert call_count == 2

    def test_async_success(self) -> None:
        @circuit_breaker(fail_threshold=2)
        async def async_succeed() -> str:
            return "ok"

        assert asyncio.run(async_succeed()) == "ok"
        assert async_succeed.state == CircuitState.CLOSED
