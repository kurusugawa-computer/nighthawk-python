from __future__ import annotations

import asyncio

import pytest

from nighthawk.resilience import plurality, vote


@pytest.mark.parametrize("asynchronous", [False, True])
def test_exception_values_are_successful_results(asynchronous: bool) -> None:
    value = ValueError("a value, not a raised failure")

    def result() -> ValueError:
        return value

    async def async_result() -> ValueError:
        return value

    if asynchronous:
        assert asyncio.run(vote(count=3)(async_result)()) is value
    else:
        assert vote(count=3)(result)() is value


@pytest.mark.parametrize("exception_type", [asyncio.CancelledError, BaseException, KeyboardInterrupt, SystemExit])
@pytest.mark.asyncio
async def test_child_interruption_is_not_masked_by_successful_votes(exception_type: type[BaseException]) -> None:
    call_count = 0
    finished: list[int] = []
    original_exception = exception_type("stop")

    async def operation() -> str:
        nonlocal call_count
        call_count += 1
        call_number = call_count
        try:
            if call_number == 1:
                raise original_exception
            await asyncio.sleep(0)
            return "ok"
        finally:
            finished.append(call_number)

    with pytest.raises(exception_type) as caught:
        await vote(count=3)(operation)()
    assert caught.value is original_exception
    assert sorted(finished) == [1, 2, 3]


@pytest.mark.asyncio
async def test_caller_cancellation_joins_every_vote() -> None:
    started: list[int] = []
    finished: list[int] = []
    all_started = asyncio.Event()
    pending = asyncio.Event()

    async def operation() -> str:
        call_number = len(started)
        started.append(call_number)
        if len(started) == 3:
            all_started.set()
        try:
            await pending.wait()
            return "ok"
        finally:
            await asyncio.sleep(0)
            finished.append(call_number)

    task = asyncio.create_task(vote(count=3)(operation)())
    await asyncio.wait_for(all_started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(finished) == [0, 1, 2]


class TestPlurality:
    def test_simple_majority(self) -> None:
        assert plurality(["a", "b", "a"]) == "a"

    def test_single_element(self) -> None:
        assert plurality(["x"]) == "x"

    def test_all_same(self) -> None:
        assert plurality(["y", "y", "y"]) == "y"

    def test_unhashable_values(self) -> None:
        result = plurality([{"a": 1}, {"b": 2}, {"a": 1}])
        assert result == {"a": 1}

    def test_empty_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="requires at least one result"):
            plurality([])


class TestVoteSync:
    def test_majority_wins(self) -> None:
        call_count = 0

        def classify(text: str) -> str:
            nonlocal call_count
            call_count += 1
            return "a" if call_count != 2 else "b"

        result = vote(count=3)(classify)("hello")
        assert result == "a"
        assert call_count == 3

    def test_all_agree(self) -> None:
        def constant(x: str) -> str:
            return "same"

        result = vote(count=3)(constant)("hello")
        assert result == "same"

    def test_partial_failures_still_succeed(self) -> None:
        call_count = 0

        def sometimes_fail(x: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("transient")
            return "ok"

        result = vote(count=3)(sometimes_fail)("hello")
        assert result == "ok"

    def test_insufficient_success_raises(self) -> None:
        call_count = 0

        def mostly_fail(x: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ValueError(f"fail #{call_count}")
            return "ok"

        with pytest.raises(ValueError, match="fail #2"):
            vote(count=3)(mostly_fail)("hello")

    def test_all_fail_raises_last_exception(self) -> None:
        call_count = 0

        def always_fail(x: str) -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError(f"fail #{call_count}")

        with pytest.raises(ValueError, match="fail #3"):
            vote(count=3)(always_fail)("hello")

    def test_custom_decide_function(self) -> None:
        call_count = 0

        def varying(x: str) -> int:
            nonlocal call_count
            call_count += 1
            return call_count * 10

        result = vote(count=3, decide=max)(varying)("hello")
        assert result == 30

    def test_custom_min_success(self) -> None:
        call_count = 0

        def mostly_fail(x: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ValueError("fail")
            return "ok"

        result = vote(count=3, min_success=1)(mostly_fail)("hello")
        assert result == "ok"

    def test_count_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="must be at least 1"):
            vote(count=0)

    def test_min_success_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="min_success must be at least 1"):
            vote(count=3, min_success=0)

    def test_min_success_must_not_exceed_count(self) -> None:
        with pytest.raises(ValueError, match="less than or equal to count"):
            vote(count=3, min_success=4)

    def test_preserves_metadata(self) -> None:
        def my_function(x: str) -> str:
            """My doc."""
            return x

        wrapped = vote(count=3)(my_function)
        assert wrapped.__name__ == "my_function"
        assert wrapped.__doc__ == "My doc."


class TestVoteAsync:
    def test_async_concurrent_execution(self) -> None:
        call_count = 0

        async def classify(x: str) -> str:
            nonlocal call_count
            call_count += 1
            return "result"

        result = asyncio.run(vote(count=3)(classify)("hello"))
        assert result == "result"
        assert call_count == 3

    def test_async_partial_failures(self) -> None:
        call_count = 0

        async def sometimes_fail(x: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("transient")
            return "ok"

        result = asyncio.run(vote(count=3)(sometimes_fail)("hello"))
        assert result == "ok"

    def test_async_all_fail(self) -> None:
        async def always_fail(x: str) -> str:
            raise ValueError("always")

        with pytest.raises(ValueError, match="always"):
            asyncio.run(vote(count=3)(always_fail)("hello"))
