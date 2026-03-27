from __future__ import annotations

import asyncio
import time

import pytest

from nighthawk.resilience import timeout

# ---------------------------------------------------------------------------
# Decorator form — sync
# ---------------------------------------------------------------------------


class TestTimeoutDecoratorSync:
    def test_completes_within_timeout(self) -> None:
        @timeout(seconds=5)
        def fast() -> str:
            return "ok"

        assert fast() == "ok"

    def test_raises_timeout_error_on_slow_function(self) -> None:
        @timeout(seconds=0.1)
        def slow() -> str:
            time.sleep(5)
            return "ok"

        with pytest.raises(TimeoutError, match="timed out"):
            slow()

    def test_preserves_function_metadata(self) -> None:
        def my_function() -> str:
            """My docstring."""
            return "ok"

        wrapped = timeout(seconds=5)(my_function)
        assert wrapped.__name__ == "my_function"
        assert wrapped.__doc__ == "My docstring."

    def test_passes_arguments(self) -> None:
        @timeout(seconds=5)
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5

    def test_propagates_exception(self) -> None:
        @timeout(seconds=5)
        def failing() -> str:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            failing()


# ---------------------------------------------------------------------------
# Decorator form — async
# ---------------------------------------------------------------------------


class TestTimeoutDecoratorAsync:
    def test_async_completes_within_timeout(self) -> None:
        @timeout(seconds=5)
        async def fast() -> str:
            return "ok"

        assert asyncio.run(fast()) == "ok"

    def test_async_raises_timeout_error(self) -> None:
        @timeout(seconds=0.1)
        async def slow() -> str:
            await asyncio.sleep(5)
            return "ok"

        with pytest.raises(TimeoutError):
            asyncio.run(slow())

    def test_async_preserves_metadata(self) -> None:
        async def my_async() -> str:
            """Async doc."""
            return "ok"

        wrapped = timeout(seconds=5)(my_async)
        assert wrapped.__name__ == "my_async"
        assert wrapped.__doc__ == "Async doc."


# ---------------------------------------------------------------------------
# Async context manager form
# ---------------------------------------------------------------------------


class TestTimeoutAsyncContextManager:
    def test_async_with_completes(self) -> None:
        async def run() -> str:
            async with timeout(seconds=5):
                return "ok"

        assert asyncio.run(run()) == "ok"

    def test_async_with_times_out(self) -> None:
        async def run() -> str:
            async with timeout(seconds=0.1):
                await asyncio.sleep(5)
                return "ok"

        with pytest.raises(TimeoutError):
            asyncio.run(run())


# ---------------------------------------------------------------------------
# Sync context manager — not supported
# ---------------------------------------------------------------------------


class TestTimeoutSyncContextManager:
    def test_sync_context_manager_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="Sync timeout context manager is not supported"), timeout(seconds=5):
            pass
