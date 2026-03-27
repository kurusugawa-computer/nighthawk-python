from __future__ import annotations

import asyncio

from tenacity import wait_none

import nighthawk as nh
from nighthawk.resilience import (
    CircuitOpenError,
    circuit_breaker,
    fallback,
    retrying,
    timeout,
    vote,
)
from nighthawk.testing import ScriptedExecutor, pass_response, raise_response

# ---------------------------------------------------------------------------
# Composition tests
# ---------------------------------------------------------------------------


class TestRetryWithVote:
    def test_retry_wraps_vote(self) -> None:
        call_count = 0

        def classify(_text: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                # First vote round: all fail
                raise ValueError("fail")
            return "ok"

        # vote(count=3) fails on first round (all 3 fail), retrying catches it
        composed = retrying(attempts=2, on=ValueError, wait=wait_none())(vote(count=3, min_success=1)(classify))
        result = composed("hello")
        assert result == "ok"
        # First round: 3 calls all fail; second round: 3 calls all succeed
        assert call_count == 6


class TestFallbackWithRetry:
    def test_fallback_with_retry(self) -> None:
        primary_count = 0
        backup_count = 0

        def primary(_text: str) -> str:
            nonlocal primary_count
            primary_count += 1
            raise ValueError("primary always fails")

        def backup(_text: str) -> str:
            nonlocal backup_count
            backup_count += 1
            if backup_count == 1:
                raise ValueError("backup transient")
            return "backup_ok"

        composed = fallback(
            retrying(attempts=2, on=ValueError, wait=wait_none())(primary),
            retrying(attempts=2, on=ValueError, wait=wait_none())(backup),
        )
        result = composed("hello")
        assert result == "backup_ok"
        assert primary_count == 2  # retried once
        assert backup_count == 2  # retried once


class TestTimeoutWithRetry:
    def test_retry_after_timeout(self) -> None:
        call_count = 0

        def slow_then_fast(_text: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                import time

                time.sleep(5)
            return "ok"

        composed = retrying(attempts=2, on=TimeoutError, wait=wait_none())(timeout(seconds=0.1)(slow_then_fast))
        result = composed("hello")
        assert result == "ok"
        assert call_count == 2


class TestCircuitBreakerWithFallback:
    def test_circuit_open_triggers_fallback(self) -> None:
        primary_count = 0

        @circuit_breaker(fail_threshold=2, on=ValueError)
        def primary(_text: str) -> str:
            nonlocal primary_count
            primary_count += 1
            raise ValueError("fail")

        def backup(_text: str) -> str:
            return "backup"

        composed = fallback(primary, backup, on=(ValueError, CircuitOpenError))

        # First two calls: primary fails (opens circuit)
        assert composed("a") == "backup"
        assert composed("b") == "backup"
        assert primary.state.value == "open"

        # Third call: circuit open, CircuitOpenError caught by fallback
        assert composed("c") == "backup"
        assert primary_count == 2  # Not called when circuit is open


class TestFullComposition:
    def test_full_stack(self) -> None:
        """fallback(retrying(vote(f1)), retrying(f2), default=x)"""
        primary_count = 0
        backup_count = 0

        def primary(_text: str) -> str:
            nonlocal primary_count
            primary_count += 1
            raise ValueError("primary always fails")

        def backup(_text: str) -> str:
            nonlocal backup_count
            backup_count += 1
            return "backup_result"

        composed = fallback(
            retrying(attempts=2, on=ValueError, wait=wait_none())(vote(count=3, min_success=1)(primary)),
            retrying(attempts=2, on=ValueError, wait=wait_none())(backup),
            default="unknown",
        )
        result = composed("hello")
        assert result == "backup_result"

    def test_full_stack_falls_to_default(self) -> None:
        def always_fail(_text: str) -> str:
            raise ValueError("fail")

        composed = fallback(
            retrying(attempts=2, on=ValueError, wait=wait_none())(always_fail),
            retrying(attempts=2, on=ValueError, wait=wait_none())(always_fail),
            default="unknown",
        )
        result = composed("hello")
        assert result == "unknown"


class TestCompositionAsync:
    def test_async_composition(self) -> None:
        primary_count = 0

        async def primary(_text: str) -> str:
            nonlocal primary_count
            primary_count += 1
            if primary_count <= 2:
                raise ValueError("fail")
            return "ok"

        async def backup(_text: str) -> str:
            return "backup"

        composed = fallback(
            retrying(attempts=3, on=ValueError, wait=wait_none())(primary),
            backup,
        )
        result = asyncio.run(composed("hello"))
        assert result == "ok"
        assert primary_count == 3


class TestCompositionWithNaturalFunction:
    def test_natural_function_with_resilience(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                raise_response("transient error 1"),
                raise_response("transient error 2"),
                pass_response(result="classified"),
            ]
        )

        @nh.natural_function
        def primary(text: str) -> str:
            result: str = ""
            """natural
            Classify <text> and set <:result>.
            """
            return result

        @nh.natural_function
        def backup(text: str) -> str:
            result: str = ""
            """natural
            Simple classify <text> and set <:result>.
            """
            return result

        composed = fallback(
            retrying(attempts=2, wait=wait_none())(primary),
            backup,
            default="unknown",
        )

        with nh.run(executor):
            result = composed("hello")

        # primary fails twice (retrying exhausted), backup gets 3rd response
        assert result == "classified"
