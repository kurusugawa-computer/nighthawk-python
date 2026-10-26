from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import logging
from collections.abc import Callable, Generator
from decimal import Decimal

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.resilience import BudgetExceededError, budget
from nighthawk.runtime import scoping as runtime_scoping
from nighthawk.runtime.scoping import get_usage_meter, span
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


def _find_finished_event(run_span_exporter: InMemorySpanExporter, *, event_name: str) -> object:
    for span_data in run_span_exporter.get_finished_spans():
        for event in span_data.events:
            if event.name == event_name:
                return event
    raise AssertionError(f"Event not found: {event_name}")


def _make_function_that_records_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> ...:
    def fn(text: str) -> str:
        meter = get_usage_meter()
        meter.record(RunUsage(input_tokens=input_tokens, output_tokens=output_tokens))
        return f"result:{text}"

    return fn


def _make_async_function_that_records_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> ...:
    async def fn(text: str) -> str:
        meter = get_usage_meter()
        meter.record(RunUsage(input_tokens=input_tokens, output_tokens=output_tokens))
        return f"result:{text}"

    return fn


class TestBudgetPreCheck:
    def test_blocks_when_cumulative_budget_already_exceeded(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=10, output_tokens=10)
        budgeted = budget(tokens=50)(fn)

        with nh.run(StubExecutor()):
            meter = get_usage_meter()
            meter.record(RunUsage(input_tokens=30, output_tokens=30))

            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens"
            assert exception_info.value.limit_value == 50

    def test_allows_call_when_under_budget(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=5, output_tokens=5)
        budgeted = budget(tokens=100)(fn)

        with nh.run(StubExecutor()):
            result = budgeted("hello")
            assert result == "result:hello"

    def test_blocks_when_exactly_at_budget(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=10, output_tokens=10)
        budgeted = budget(tokens=50)(fn)

        with nh.run(StubExecutor()):
            meter = get_usage_meter()
            meter.record(RunUsage(input_tokens=25, output_tokens=25))

            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens"


class TestBudgetPostCheck:
    def test_raises_when_tokens_per_call_exceeded(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=100, output_tokens=100)
        budgeted = budget(tokens_per_call=50)(fn)

        with nh.run(StubExecutor()):
            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens_per_call"
            assert exception_info.value.limit_value == 50
            assert exception_info.value.call_usage.total_tokens == 200

    def test_raises_when_cumulative_budget_exceeded_after_call(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=30, output_tokens=30)
        budgeted = budget(tokens=100)(fn)

        with nh.run(StubExecutor()):
            meter = get_usage_meter()
            meter.record(RunUsage(input_tokens=25, output_tokens=25))

            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens"
            assert exception_info.value.accumulated_usage.total_tokens == 110

    def test_allows_call_within_per_call_budget(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=10, output_tokens=10)
        budgeted = budget(tokens_per_call=50)(fn)

        with nh.run(StubExecutor()):
            result = budgeted("hello")
            assert result == "result:hello"


class TestBudgetCombined:
    def test_tokens_per_call_checked_before_cumulative(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=100, output_tokens=100)
        budgeted = budget(tokens=50, tokens_per_call=50)(fn)

        with nh.run(StubExecutor()):
            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens_per_call"

    def test_cumulative_checked_when_per_call_passes(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=20, output_tokens=20)
        budgeted = budget(tokens=50, tokens_per_call=100)(fn)

        with nh.run(StubExecutor()):
            meter = get_usage_meter()
            meter.record(RunUsage(input_tokens=10, output_tokens=10))

            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens"


class TestBudgetAsync:
    def test_async_pre_check_blocks(self) -> None:
        fn = _make_async_function_that_records_usage(input_tokens=10, output_tokens=10)
        budgeted = budget(tokens=50)(fn)

        with nh.run(StubExecutor()):
            meter = get_usage_meter()
            meter.record(RunUsage(input_tokens=30, output_tokens=30))

            with pytest.raises(BudgetExceededError) as exception_info:
                asyncio.run(budgeted("x"))

            assert exception_info.value.limit_kind == "tokens"

    def test_async_post_check_tokens_per_call(self) -> None:
        fn = _make_async_function_that_records_usage(input_tokens=100, output_tokens=100)
        budgeted = budget(tokens_per_call=50)(fn)

        with nh.run(StubExecutor()):
            with pytest.raises(BudgetExceededError) as exception_info:
                asyncio.run(budgeted("x"))

            assert exception_info.value.limit_kind == "tokens_per_call"

    def test_async_allows_call_under_budget(self) -> None:
        fn = _make_async_function_that_records_usage(input_tokens=5, output_tokens=5)
        budgeted = budget(tokens=100)(fn)

        with nh.run(StubExecutor()):
            result = asyncio.run(budgeted("hello"))
            assert result == "result:hello"


class TestBudgetNoRunContext:
    def test_no_enforcement_without_run_context(self) -> None:
        def function(value: str) -> str:
            with pytest.raises(nh.NighthawkError):
                get_usage_meter()
            return f"result:{value}"

        assert budget(tokens=0)(function)("hello") == "result:hello"

    def test_async_no_enforcement_without_run_context(self) -> None:
        async def function(value: str) -> str:
            with pytest.raises(nh.NighthawkError):
                get_usage_meter()
            return f"result:{value}"

        assert asyncio.run(budget(tokens=0)(function)("hello")) == "result:hello"


class TestBudgetExceededErrorAttributes:
    def test_budget_exceeded_error_is_nighthawk_error(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=100, output_tokens=100)
        budgeted = budget(tokens_per_call=50)(fn)

        with nh.run(StubExecutor()), pytest.raises(nh.NighthawkError):
            budgeted("x")

    def test_budget_exceeded_event_includes_limit_metadata(self, run_span_exporter: InMemorySpanExporter) -> None:
        fn = _make_function_that_records_usage(input_tokens=100, output_tokens=100)
        budgeted = budget(tokens_per_call=50)(fn)

        with nh.run(StubExecutor()), span("budget-span"), pytest.raises(BudgetExceededError):
            budgeted("x")

        budget_exceeded_event = _find_finished_event(run_span_exporter, event_name="nighthawk.resilience.budget.exceeded")
        attributes = getattr(budget_exceeded_event, "attributes", None)
        assert attributes is not None
        assert attributes["nighthawk.resilience.budget.limit_kind"] == "tokens_per_call"
        assert attributes["nighthawk.resilience.budget.call_total_tokens"] == 200

    def test_error_has_correct_attributes(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=100, output_tokens=100)
        budgeted = budget(tokens_per_call=50)(fn)

        with nh.run(StubExecutor()):
            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            error = exception_info.value
            assert error.limit_kind == "tokens_per_call"
            assert error.limit_value == 50
            assert error.call_usage.input_tokens == 100
            assert error.call_usage.output_tokens == 100
            assert error.accumulated_usage.total_tokens == 200

    def test_error_message_is_descriptive(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=50, output_tokens=50)
        budgeted = budget(tokens_per_call=30)(fn)

        with nh.run(StubExecutor()), pytest.raises(BudgetExceededError, match="tokens_per_call limit 30"):
            budgeted("x")


class TestBudgetLogging:
    def test_logs_warning_on_budget_exceeded(self, caplog: pytest.LogCaptureFixture) -> None:
        fn = _make_function_that_records_usage(input_tokens=100, output_tokens=100)
        budgeted = budget(tokens_per_call=50)(fn)

        with nh.run(StubExecutor()), caplog.at_level(logging.WARNING, logger="nighthawk.resilience"), pytest.raises(BudgetExceededError):
            budgeted("x")

        assert any("Budget exceeded" in record.message for record in caplog.records)


class TestBudgetValidation:
    def test_raises_value_error_when_no_limits_specified(self) -> None:
        with pytest.raises(ValueError, match="requires at least one"):
            budget()

    def test_raises_value_error_when_cost_without_cost_function(self) -> None:
        with pytest.raises(ValueError, match="cost_function"):
            budget(cost=5.0)

    def test_raises_value_error_when_cost_per_call_without_cost_function(self) -> None:
        with pytest.raises(ValueError, match="cost_function"):
            budget(cost_per_call=1.0)


class TestBudgetMetadata:
    def test_preserves_function_name(self) -> None:
        def my_classifier(text: str) -> str:
            return text

        wrapped = budget(tokens=100)(my_classifier)
        assert wrapped.__name__ == "my_classifier"

    def test_preserves_function_doc(self) -> None:
        def my_classifier(text: str) -> str:
            """Classify text."""
            return text

        wrapped = budget(tokens=100)(my_classifier)
        assert wrapped.__doc__ == "Classify text."


def _dollar_per_thousand_tokens(usage: RunUsage) -> float:
    return usage.total_tokens * 0.01 / 1000


class TestBudgetEstimate:
    def test_estimate_blocks_before_execution(self) -> None:
        call_count = 0

        def expensive(text: str) -> str:
            nonlocal call_count
            call_count += 1
            meter = get_usage_meter()
            meter.record(RunUsage(input_tokens=5, output_tokens=5))
            return f"result:{text}"

        def estimate_usage(*_: object) -> RunUsage:
            return RunUsage(input_tokens=40, output_tokens=40)

        budgeted = budget(tokens=50, estimate_usage=estimate_usage)(expensive)

        with nh.run(StubExecutor()):
            meter = get_usage_meter()
            meter.record(RunUsage(input_tokens=15, output_tokens=15))

            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens"
            assert call_count == 0


class TestBudgetCostFunction:
    def test_cost_limit_blocks_when_exceeded(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=500, output_tokens=500)
        budgeted = budget(cost=0.005, cost_function=_dollar_per_thousand_tokens)(fn)

        with nh.run(StubExecutor()):
            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "cost"
            assert exception_info.value.limit_value == 0.005

    def test_cost_per_call_limit(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=500, output_tokens=500)
        budgeted = budget(cost_per_call=0.005, cost_function=_dollar_per_thousand_tokens)(fn)

        with nh.run(StubExecutor()):
            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "cost_per_call"
            assert exception_info.value.limit_value == 0.005

    def test_cost_allows_call_under_budget(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=10, output_tokens=10)
        budgeted = budget(cost=1.0, cost_function=_dollar_per_thousand_tokens)(fn)

        with nh.run(StubExecutor()):
            result = budgeted("hello")
            assert result == "result:hello"

    def test_cost_pre_check_blocks_when_already_exceeded(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=10, output_tokens=10)
        budgeted = budget(cost=0.005, cost_function=_dollar_per_thousand_tokens)(fn)

        with nh.run(StubExecutor()):
            meter = get_usage_meter()
            meter.record(RunUsage(input_tokens=500, output_tokens=500))

            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "cost"

    def test_cost_function_receives_correct_usage(self) -> None:
        received_usages: list[RunUsage] = []

        def tracking_cost_function(usage: RunUsage) -> float:
            received_usages.append(usage)
            return usage.total_tokens * 0.01 / 1000

        fn = _make_function_that_records_usage(input_tokens=100, output_tokens=50)
        budgeted = budget(cost=10.0, cost_function=tracking_cost_function)(fn)

        with nh.run(StubExecutor()):
            budgeted("hello")

        assert len(received_usages) > 0
        assert any(u.total_tokens == 150 for u in received_usages)


type _BudgetTarget = Callable[[Callable[..., object]], Callable[..., object]]


@pytest.fixture(params=[False, True], ids=["synchronous", "asynchronous"])
def budget_target(request: pytest.FixtureRequest) -> _BudgetTarget:
    def decorate(function: Callable[..., object]) -> Callable[..., object]:
        if request.param:

            async def asynchronous(*arguments: object, **keywords: object) -> object:
                return function(*arguments, **keywords)

            return asynchronous
        return function

    return decorate


@pytest.mark.asyncio
async def test_cost_per_call_receives_complete_usage_delta(budget_target: _BudgetTarget) -> None:
    previous = RunUsage(requests=3, tool_calls=5, input_tokens=100, details={"reasoning_tokens": 10}, cost=Decimal("0.50"))
    recorded = RunUsage(
        requests=2,
        tool_calls=4,
        input_tokens=20,
        output_tokens=10,
        cache_read_tokens=6,
        cache_write_tokens=3,
        input_audio_tokens=8,
        cache_audio_read_tokens=2,
        output_audio_tokens=5,
        audio_seconds=1.5,
        details={"reasoning_tokens": 7},
        cost=Decimal("0.25"),
        web_searches=3,
    )
    observed: list[RunUsage] = []

    def request_cost(usage: RunUsage) -> float:
        observed.append(usage)
        return usage.requests * 0.1

    @budget_target
    def function() -> None:
        get_usage_meter().record(recorded)

    with nh.run(StubExecutor()):
        meter = get_usage_meter()
        meter.record(previous)
        with pytest.raises(BudgetExceededError) as caught:
            result = budget(cost_per_call=0.15, cost_function=request_cost)(function)()
            if inspect.isawaitable(result):
                await result
        assert observed == [recorded]
        assert caught.value.call_usage == recorded
        assert caught.value.accumulated_usage == previous + recorded
        observed[0].details["reasoning_tokens"] = 1000
        assert meter.snapshot().details == {"reasoning_tokens": 17}
        assert previous.details == {"reasoning_tokens": 10}
        assert recorded.details == {"reasoning_tokens": 7}


@pytest.mark.asyncio
@pytest.mark.parametrize("cost_limit, allowed", [(3.0, False), (4.0, True)])
async def test_estimated_cost_uses_predicted_total(budget_target: _BudgetTarget, cost_limit: float, allowed: bool) -> None:
    calls: list[str] = []
    estimates: list[tuple[tuple[object, ...], dict[str, object]]] = []

    @budget_target
    def function(text: str, *, repetitions: int) -> str:
        calls.append(text)
        get_usage_meter().record(RunUsage(input_tokens=repetitions))
        return text * repetitions

    def estimate_usage(arguments: tuple[object, ...], keywords: dict[str, object]) -> RunUsage:
        estimates.append((arguments, keywords))
        return RunUsage(input_tokens=1)

    wrapped = budget(cost=cost_limit, cost_function=lambda usage: float(usage.total_tokens**2), estimate_usage=estimate_usage)(function)
    with nh.run(StubExecutor()):
        get_usage_meter().record(RunUsage(input_tokens=1))
        if allowed:
            result = wrapped("text", repetitions=1)
            if inspect.isawaitable(result):
                result = await result
            assert result == "text"
        else:
            with pytest.raises(BudgetExceededError) as caught:
                result = wrapped("text", repetitions=1)
                if inspect.isawaitable(result):
                    await result
            assert caught.value.limit_kind == "cost"
            assert caught.value.accumulated_usage.total_tokens == 2
        assert calls == (["text"] if allowed else [])
        assert estimates == [(("text",), {"repetitions": 1})]
        assert get_usage_meter().total_tokens == (2 if allowed else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("function failed"), asyncio.CancelledError("caller interrupted")])
async def test_budget_preserves_failure_and_recorded_usage(budget_target: _BudgetTarget, failure: BaseException) -> None:
    @budget_target
    def function() -> None:
        get_usage_meter().record(RunUsage(input_tokens=10))
        raise failure

    with nh.run(StubExecutor()):
        with pytest.raises(type(failure)) as caught:
            result = budget(tokens_per_call=1)(function)()
            if inspect.isawaitable(result):
                await result
        assert caught.value is failure
        assert get_usage_meter().total_tokens == 10


@pytest.mark.asyncio
async def test_budget_excludes_usage_in_a_separate_nested_meter(budget_target: _BudgetTarget) -> None:
    nested_meter = nh.UsageMeter()

    @budget_target
    def function() -> str:
        with nh.scope(usage_meter=nested_meter):
            get_usage_meter().record(RunUsage(input_tokens=100))
        get_usage_meter().record(RunUsage(input_tokens=1))
        return "completed"

    with nh.run(StubExecutor()):
        result = budget(tokens_per_call=1)(function)()
        if inspect.isawaitable(result):
            result = await result
        assert result == "completed"
        assert get_usage_meter().total_tokens == 1
        assert nested_meter.total_tokens == 100


@pytest.mark.asyncio
async def test_budget_skips_estimation_outside_a_run(budget_target: _BudgetTarget) -> None:
    @budget_target
    def function() -> str:
        return "completed"

    def estimate_usage(*arguments: object) -> RunUsage:
        raise AssertionError("Estimation requires an active meter")

    result = budget(tokens=0, estimate_usage=estimate_usage)(function)()
    if inspect.isawaitable(result):
        result = await result
    assert result == "completed"
