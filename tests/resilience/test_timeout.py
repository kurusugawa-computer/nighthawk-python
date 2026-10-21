from __future__ import annotations

import asyncio
import importlib.metadata
import time
from collections.abc import Callable, Coroutine, Generator
from contextvars import ContextVar
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import nighthawk as nh
from nighthawk.resilience import timeout
from nighthawk.runtime import scoping as runtime_scoping
from nighthawk.testing import ScriptedExecutor, pass_response
from tests.support.executors import StubExecutor


@pytest.fixture
def run_span_exporter() -> Generator[InMemorySpanExporter]:
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


class TestTimeoutTracing:
    @pytest.mark.parametrize("asynchronous", [False, True])
    def test_application_timeout_is_preserved_without_deadline_event(self, run_span_exporter: InMemorySpanExporter, asynchronous: bool) -> None:
        original_exception = TimeoutError("upstream deadline")

        def failing() -> None:
            raise original_exception

        async def failing_async() -> None:
            failing()

        with pytest.raises(TimeoutError) as caught:
            if asynchronous:
                _call_timeout_async_function(timeout(seconds=5)(failing_async))
            else:
                _call_timeout_sync_function(timeout(seconds=5)(failing))

        assert caught.value is original_exception
        assert _timeout_event_list(_get_finished_run_spans(run_span_exporter)[0]) == []

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


class TestTimeoutDecoratorSync:
    def test_copies_context_without_leaking_worker_rebindings(self) -> None:
        selected_value = ContextVar("selected_value", default="outside")

        @timeout(seconds=5)
        def read_and_replace() -> str:
            value = selected_value.get()
            selected_value.set("worker")
            return value

        token = selected_value.set("caller")
        try:
            assert read_and_replace() == "caller"
            assert selected_value.get() == "caller"
        finally:
            selected_value.reset(token)

    def test_natural_function_inherits_run_and_host_scope(self) -> None:
        executor = ScriptedExecutor([pass_response(binding_name_to_value={"result": "shared"})])
        events: list[nh.lifecycle.StepFinished] = []
        with nh.run(executor), nh.scope(task_references={"value": "shared"}, lifecycle=nh.lifecycle.StepLifecycle(events.append)):
            assert timeout(seconds=5)(_read_scoped_value)() == "shared"

        assert executor.calls[0].input_binding_name_to_value == {"value": "shared"}
        assert len(events) == 1 and isinstance(events[0], nh.lifecycle.StepCompleted)

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


class TestTimeoutAsyncContextManager:
    @pytest.mark.asyncio
    async def test_shared_handle_can_be_nested(self) -> None:
        handle = timeout(seconds=5)
        async with handle:
            async with handle:
                await asyncio.sleep(0)
            await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_shared_handle_has_separate_concurrent_scopes(self) -> None:
        handle = timeout(seconds=5)
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        first_finished = asyncio.Event()

        async def first() -> None:
            async with handle:
                first_entered.set()
                await second_entered.wait()
            first_finished.set()

        async def second() -> None:
            await first_entered.wait()
            async with handle:
                second_entered.set()
                await first_finished.wait()

        await asyncio.gather(first(), second())

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


class TestTimeoutSyncContextManager:
    def test_sync_context_manager_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="Sync timeout context manager is not supported"), timeout(seconds=5):
            pass


@nh.function
def _read_scoped_value() -> str:
    result = ""
    """execute
    Use <value> to fill <:result>.
    """
    return result
