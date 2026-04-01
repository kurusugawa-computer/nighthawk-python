from __future__ import annotations

import asyncio
import importlib.metadata
import time
from collections.abc import Callable, Coroutine, Generator
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import nighthawk as nh
from nighthawk.resilience import timeout
from nighthawk.runtime import scoping as runtime_scoping
from tests.execution.stub_executor import StubExecutor


@pytest.fixture
def run_span_exporter() -> Generator[InMemorySpanExporter, None, None]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    previous_tracer_provider = runtime_scoping._tracer
    runtime_scoping._tracer = tracer_provider.get_tracer("nighthawk", importlib.metadata.version("nighthawk-python"))
    try:
        yield span_exporter
    finally:
        runtime_scoping._tracer = previous_tracer_provider


def _get_finished_run_spans(run_span_exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span_data for span_data in run_span_exporter.get_finished_spans() if span_data.name == "nighthawk.run"]


def _call_timeout_sync_function(function: Callable[[], object]) -> object:
    with nh.run(StubExecutor()):
        return function()


def _call_timeout_async_function(function: Callable[[], Coroutine[Any, Any, object]]) -> object:
    with nh.run(StubExecutor()):
        return asyncio.run(function())


def _timeout_event_list(run_span: ReadableSpan) -> list[tuple[str, dict[str, object]]]:
    timeout_event_list: list[tuple[str, dict[str, object]]] = []
    for event in run_span.events:
        if event.name != "nighthawk.resilience.timeout.triggered":
            continue
        timeout_event_list.append((event.name, dict(event.attributes or {})))
    return timeout_event_list


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


class TestTimeoutTracing:
    def test_sync_timeout_emits_event(self, run_span_exporter: InMemorySpanExporter) -> None:
        @timeout(seconds=0.01)
        def slow() -> str:
            time.sleep(0.1)
            return "ok"

        with pytest.raises(TimeoutError):
            _call_timeout_sync_function(slow)

        run_span = _get_finished_run_spans(run_span_exporter)[0]
        timeout_event_list = _timeout_event_list(run_span)
        assert len(timeout_event_list) == 1
        _, attributes = timeout_event_list[0]
        assert attributes["nighthawk.resilience.timeout.mode"] == "sync"

    def test_async_timeout_emits_event(self, run_span_exporter: InMemorySpanExporter) -> None:
        @timeout(seconds=0.01)
        async def slow() -> str:
            await asyncio.sleep(0.1)
            return "ok"

        with pytest.raises(TimeoutError):
            _call_timeout_async_function(slow)

        run_span = _get_finished_run_spans(run_span_exporter)[0]
        timeout_event_list = _timeout_event_list(run_span)
        assert len(timeout_event_list) == 1
        _, attributes = timeout_event_list[0]
        assert attributes["nighthawk.resilience.timeout.mode"] == "async"

    def test_no_timeout_no_event(self, run_span_exporter: InMemorySpanExporter) -> None:
        @timeout(seconds=1)
        def fast() -> str:
            return "ok"

        _call_timeout_sync_function(fast)

        run_span = _get_finished_run_spans(run_span_exporter)[0]
        timeout_event_list = _timeout_event_list(run_span)
        assert timeout_event_list == []


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
