from __future__ import annotations

import asyncio
import inspect

import pytest

from nighthawk.tools.execution import _run_tool_and_normalize


def test_normalization_failure_does_not_dispose_application_value(monkeypatch: pytest.MonkeyPatch) -> None:
    async def value() -> int:
        return 7

    retained = value()

    async def tool_call():
        return retained

    def fail(value: object) -> None:
        assert value is retained
        raise ValueError("normalization failed")

    monkeypatch.setattr("nighthawk.tools.execution._normalize_tool_success", fail)
    try:
        with pytest.raises(ValueError, match="normalization failed"):
            asyncio.run(_run_tool_and_normalize(tool_call))
        assert inspect.getcoroutinestate(retained) == inspect.CORO_CREATED
    finally:
        retained.close()
