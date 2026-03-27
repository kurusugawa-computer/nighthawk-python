from __future__ import annotations

import asyncio
import inspect

import pytest

from nighthawk.resilience import fallback

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class TestFallbackSync:
    def test_returns_first_successful_result(self) -> None:
        def primary(x: str) -> str:
            return f"primary:{x}"

        def backup(x: str) -> str:
            return f"backup:{x}"

        composed = fallback(primary, backup)
        assert composed("a") == "primary:a"

    def test_falls_back_to_second_on_failure(self) -> None:
        def primary(x: str) -> str:
            raise ValueError("primary failed")

        def backup(x: str) -> str:
            return f"backup:{x}"

        composed = fallback(primary, backup)
        assert composed("a") == "backup:a"

    def test_falls_back_to_default(self) -> None:
        def primary(x: str) -> str:
            raise ValueError("fail")

        def backup(x: str) -> str:
            raise ValueError("fail")

        composed = fallback(primary, backup, default="fallback_value")
        assert composed("a") == "fallback_value"

    def test_default_none_is_valid(self) -> None:
        def primary(x: str) -> str:
            raise ValueError("fail")

        composed = fallback(primary, default=None)
        assert composed("a") is None

    def test_raises_last_exception_without_default(self) -> None:
        def primary(x: str) -> str:
            raise ValueError("primary error")

        def backup(x: str) -> str:
            raise TypeError("backup error")

        composed = fallback(primary, backup)
        with pytest.raises(TypeError, match="backup error"):
            composed("a")

    def test_custom_on_parameter(self) -> None:
        def primary(x: str) -> str:
            raise ValueError("val error")

        def backup(x: str) -> str:
            return "backup"

        # Only catch ValueError; TypeError would propagate immediately
        composed = fallback(primary, backup, on=ValueError)
        assert composed("a") == "backup"

    def test_non_matching_exception_propagates(self) -> None:
        def primary(x: str) -> str:
            raise TypeError("type error")

        def backup(x: str) -> str:
            return "backup"

        composed = fallback(primary, backup, on=ValueError)
        with pytest.raises(TypeError, match="type error"):
            composed("a")

    def test_preserves_first_function_metadata(self) -> None:
        def my_primary() -> str:
            """Primary docstring."""
            return "ok"

        def my_backup() -> str:
            return "ok"

        composed = fallback(my_primary, my_backup)
        assert composed.__name__ == "my_primary"
        assert composed.__doc__ == "Primary docstring."

    def test_requires_at_least_one_function(self) -> None:
        with pytest.raises(ValueError, match="requires at least one function"):
            fallback()

    def test_single_function_succeeds(self) -> None:
        def only(x: str) -> str:
            return x

        composed = fallback(only)
        assert composed("a") == "a"

    def test_single_function_fails_with_default(self) -> None:
        def only(x: str) -> str:
            raise ValueError("fail")

        composed = fallback(only, default="default")
        assert composed("a") == "default"

    def test_merged_return_type_when_functions_differ(self) -> None:
        def primary(x: str) -> str:
            return f"primary:{x}"

        def backup(x: str) -> int:
            return 42

        composed = fallback(primary, backup)
        signature = inspect.signature(composed)
        assert signature.return_annotation == str | int

    def test_no_merged_return_type_when_functions_match(self) -> None:
        def primary(x: str) -> str:
            return f"primary:{x}"

        def backup(x: str) -> str:
            return f"backup:{x}"

        composed = fallback(primary, backup)
        assert not hasattr(composed, "__signature__")

    def test_merged_return_type_with_three_functions(self) -> None:
        def a(x: str) -> str:
            return x

        def b(x: str) -> int:
            return 42

        def c(x: str) -> float:
            return 1.0

        composed = fallback(a, b, c)
        signature = inspect.signature(composed)
        assert signature.return_annotation == str | int | float

    def test_merged_return_type_deduplicates(self) -> None:
        def a(x: str) -> str:
            return x

        def b(x: str) -> int:
            return 42

        def c(x: str) -> str:
            return "c"

        composed = fallback(a, b, c)
        signature = inspect.signature(composed)
        assert signature.return_annotation == str | int

    def test_no_merge_when_one_function_lacks_annotation(self) -> None:
        def annotated(x: str) -> str:
            return x

        def unannotated(x):  # type: ignore[no-untyped-def]
            return 42

        composed = fallback(annotated, unannotated)
        assert not hasattr(composed, "__signature__")


# ---------------------------------------------------------------------------
# Async
# ---------------------------------------------------------------------------


class TestFallbackAsync:
    def test_async_fallback_chain(self) -> None:
        async def primary(x: str) -> str:
            raise ValueError("fail")

        async def backup(x: str) -> str:
            return f"backup:{x}"

        composed = fallback(primary, backup)
        assert asyncio.run(composed("a")) == "backup:a"

    def test_async_first_succeeds(self) -> None:
        async def primary(x: str) -> str:
            return f"primary:{x}"

        async def backup(x: str) -> str:
            return f"backup:{x}"

        composed = fallback(primary, backup)
        assert asyncio.run(composed("a")) == "primary:a"

    def test_mixed_sync_async_in_async_mode(self) -> None:
        async def primary(x: str) -> str:
            raise ValueError("fail")

        def backup_sync(x: str) -> str:
            return f"sync_backup:{x}"

        composed = fallback(primary, backup_sync)
        assert asyncio.run(composed("a")) == "sync_backup:a"  # type: ignore[arg-type]

    def test_async_falls_back_to_default(self) -> None:
        async def primary(x: str) -> str:
            raise ValueError("fail")

        composed = fallback(primary, default="default")
        assert asyncio.run(composed("a")) == "default"

    def test_async_merged_return_type(self) -> None:
        async def primary(x: str) -> str:
            return f"primary:{x}"

        async def backup(x: str) -> int:
            return 42

        composed = fallback(primary, backup)
        signature = inspect.signature(composed)
        assert signature.return_annotation == str | int
