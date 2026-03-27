from __future__ import annotations

import asyncio

import pytest

from nighthawk.resilience import plurality, vote

# ---------------------------------------------------------------------------
# plurality function
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# vote — sync
# ---------------------------------------------------------------------------


class TestVoteSync:
    def test_majority_wins(self) -> None:
        call_count = 0

        def classify(text: str) -> str:
            nonlocal call_count
            call_count += 1
            # Returns "a" twice and "b" once
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

        # count=3, min_success defaults to ceil(3/2)=2; 2 successes >= 2
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

        # count=3, min_success=2; only 1 success < 2
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

        # Only need 1 success
        result = vote(count=3, min_success=1)(mostly_fail)("hello")
        assert result == "ok"

    def test_count_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="must be at least 1"):
            vote(count=0)

    def test_preserves_metadata(self) -> None:
        def my_function(x: str) -> str:
            """My doc."""
            return x

        wrapped = vote(count=3)(my_function)
        assert wrapped.__name__ == "my_function"
        assert wrapped.__doc__ == "My doc."


# ---------------------------------------------------------------------------
# vote — async
# ---------------------------------------------------------------------------


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
