from __future__ import annotations

import asyncio

import pytest
from tenacity import RetryCallState, wait_none

import nighthawk as nh
from nighthawk.resilience import retrying
from nighthawk.testing import ScriptedExecutor, pass_response, raise_response

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _counting_function(counter: list[int], *, fail_times: int = 0, error_type: type[Exception] = nh.ExecutionError) -> ...:
    """Return a sync function that fails *fail_times* then succeeds."""

    def function() -> str:
        counter.append(1)
        if len(counter) <= fail_times:
            raise error_type(f"fail #{len(counter)}")
        return "ok"

    return function


def _async_counting_function(counter: list[int], *, fail_times: int = 0) -> ...:
    """Return an async function that fails *fail_times* then succeeds."""

    async def function() -> str:
        counter.append(1)
        if len(counter) <= fail_times:
            raise nh.ExecutionError(f"fail #{len(counter)}")
        return "ok"

    return function


# ---------------------------------------------------------------------------
# Decorator form — sync
# ---------------------------------------------------------------------------


class TestRetryingDecoratorSync:
    def test_succeeds_on_first_attempt(self) -> None:
        counter: list[int] = []
        wrapped = retrying(attempts=3, wait=wait_none())(_counting_function(counter, fail_times=0))
        assert wrapped() == "ok"
        assert len(counter) == 1

    def test_retries_on_execution_error(self) -> None:
        counter: list[int] = []
        wrapped = retrying(attempts=3, wait=wait_none())(_counting_function(counter, fail_times=2))
        assert wrapped() == "ok"
        assert len(counter) == 3

    def test_raises_after_all_attempts_exhausted(self) -> None:
        counter: list[int] = []
        wrapped = retrying(attempts=2, wait=wait_none())(_counting_function(counter, fail_times=10))
        with pytest.raises(nh.ExecutionError, match="fail #2"):
            wrapped()
        assert len(counter) == 2

    def test_does_not_retry_on_non_matching_exception(self) -> None:
        counter: list[int] = []
        wrapped = retrying(attempts=3, wait=wait_none())(_counting_function(counter, fail_times=1, error_type=ValueError))
        with pytest.raises(ValueError, match="fail #1"):
            wrapped()
        assert len(counter) == 1

    def test_custom_on_parameter(self) -> None:
        counter: list[int] = []
        wrapped = retrying(attempts=3, on=ValueError, wait=wait_none())(_counting_function(counter, fail_times=1, error_type=ValueError))
        assert wrapped() == "ok"
        assert len(counter) == 2

    def test_custom_on_tuple(self) -> None:
        counter: list[int] = []
        wrapped = retrying(attempts=3, on=(ValueError, TypeError), wait=wait_none())(_counting_function(counter, fail_times=1, error_type=TypeError))
        assert wrapped() == "ok"
        assert len(counter) == 2

    def test_preserves_function_metadata(self) -> None:
        def my_function() -> str:
            """My docstring."""
            return "ok"

        wrapped = retrying(attempts=3)(my_function)
        assert wrapped.__name__ == "my_function"
        assert wrapped.__doc__ == "My docstring."

    def test_before_sleep_callback(self) -> None:
        received_states: list[RetryCallState] = []

        def capture_state(retry_state: RetryCallState) -> None:
            received_states.append(retry_state)

        counter: list[int] = []
        wrapped = retrying(attempts=3, wait=wait_none(), before_sleep=capture_state)(_counting_function(counter, fail_times=2))
        wrapped()
        # before_sleep is called after each failed attempt (2 failures)
        assert len(received_states) == 2


# ---------------------------------------------------------------------------
# Decorator form — async
# ---------------------------------------------------------------------------


class TestRetryingDecoratorAsync:
    def test_retries_async_function(self) -> None:
        counter: list[int] = []
        wrapped = retrying(attempts=3, wait=wait_none())(_async_counting_function(counter, fail_times=2))
        assert asyncio.run(wrapped()) == "ok"
        assert len(counter) == 3

    def test_async_raises_after_exhaustion(self) -> None:
        counter: list[int] = []
        wrapped = retrying(attempts=2, wait=wait_none())(_async_counting_function(counter, fail_times=10))
        with pytest.raises(nh.ExecutionError, match="fail #2"):
            asyncio.run(wrapped())
        assert len(counter) == 2

    def test_async_preserves_metadata(self) -> None:
        async def my_async_function() -> str:
            """Async docstring."""
            return "ok"

        wrapped = retrying(attempts=3)(my_async_function)
        assert wrapped.__name__ == "my_async_function"
        assert wrapped.__doc__ == "Async docstring."


# ---------------------------------------------------------------------------
# Iterator form
# ---------------------------------------------------------------------------


class TestRetryingIteratorSync:
    def test_for_attempt_pattern_succeeds(self) -> None:
        counter: list[int] = []
        for attempt in retrying(attempts=3, wait=wait_none()):
            with attempt:
                counter.append(1)
                if len(counter) < 3:
                    raise nh.ExecutionError("not yet")
        assert len(counter) == 3

    def test_for_attempt_exhausted(self) -> None:
        counter: list[int] = []
        with pytest.raises(nh.ExecutionError):
            for attempt in retrying(attempts=2, wait=wait_none()):
                with attempt:
                    counter.append(1)
                    raise nh.ExecutionError("always fails")
        assert len(counter) == 2


class TestRetryingIteratorAsync:
    def test_async_for_attempt_pattern(self) -> None:
        counter: list[int] = []

        async def run() -> None:
            async for attempt in retrying(attempts=3, wait=wait_none()):
                with attempt:
                    counter.append(1)
                    if len(counter) < 3:
                        raise nh.ExecutionError("not yet")

        asyncio.run(run())
        assert len(counter) == 3


# ---------------------------------------------------------------------------
# Integration with @natural_function
# ---------------------------------------------------------------------------


class TestRetryingWithNaturalFunction:
    def test_retries_natural_function(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                raise_response("transient failure"),
                pass_response(result="success"),
            ]
        )

        @retrying(attempts=2, wait=wait_none())
        @nh.natural_function
        def classify(text: str) -> str:
            result: str = ""
            """natural
            Classify <text> and set <:result>.
            """
            return result

        with nh.run(executor):
            assert classify("hello") == "success"

        # First call used raise_response (ExecutionError), second used pass_response
        assert len(executor.calls) == 2
