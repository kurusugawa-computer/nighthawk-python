from __future__ import annotations

import asyncio
import importlib.metadata
import logging
from collections.abc import Generator

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.resilience import BudgetExceededError, budget
from nighthawk.runtime import scoping as runtime_scoping
from nighthawk.runtime.scoping import get_current_usage_meter, span
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


def _find_finished_event(run_span_exporter: InMemorySpanExporter, *, event_name: str) -> object:
    for span_data in run_span_exporter.get_finished_spans():
        for event in span_data.events:
            if event.name == event_name:
                return event
    raise AssertionError(f"Event not found: {event_name}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_function_that_records_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> ...:
    """Create a sync function that records usage to the current meter."""

    def fn(text: str) -> str:
        meter = get_current_usage_meter()
        if meter is not None:
            meter.record(RunUsage(input_tokens=input_tokens, output_tokens=output_tokens))
        return f"result:{text}"

    return fn


def _make_async_function_that_records_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> ...:
    """Create an async function that records usage to the current meter."""

    async def fn(text: str) -> str:
        meter = get_current_usage_meter()
        if meter is not None:
            meter.record(RunUsage(input_tokens=input_tokens, output_tokens=output_tokens))
        return f"result:{text}"

    return fn


# ---------------------------------------------------------------------------
# Pre-check tests
# ---------------------------------------------------------------------------


class TestBudgetPreCheck:
    def test_blocks_when_cumulative_budget_already_exceeded(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=10, output_tokens=10)
        budgeted = budget(tokens=50)(fn)

        with nh.run(StubExecutor()):
            meter = get_current_usage_meter()
            assert meter is not None
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
            meter = get_current_usage_meter()
            assert meter is not None
            meter.record(RunUsage(input_tokens=25, output_tokens=25))

            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens"


# ---------------------------------------------------------------------------
# Post-check tests
# ---------------------------------------------------------------------------


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
            meter = get_current_usage_meter()
            assert meter is not None
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


# ---------------------------------------------------------------------------
# Combined limits
# ---------------------------------------------------------------------------


class TestBudgetCombined:
    def test_tokens_per_call_checked_before_cumulative(self) -> None:
        """When both limits are violated, tokens_per_call is reported first."""
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
            meter = get_current_usage_meter()
            assert meter is not None
            meter.record(RunUsage(input_tokens=10, output_tokens=10))

            with pytest.raises(BudgetExceededError) as exception_info:
                budgeted("x")

            assert exception_info.value.limit_kind == "tokens"


# ---------------------------------------------------------------------------
# Async tests
# ---------------------------------------------------------------------------


class TestBudgetAsync:
    def test_async_pre_check_blocks(self) -> None:
        fn = _make_async_function_that_records_usage(input_tokens=10, output_tokens=10)
        budgeted = budget(tokens=50)(fn)

        with nh.run(StubExecutor()):
            meter = get_current_usage_meter()
            assert meter is not None
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


# ---------------------------------------------------------------------------
# No run context
# ---------------------------------------------------------------------------


class TestBudgetNoRunContext:
    def test_no_enforcement_without_run_context(self) -> None:
        fn = _make_function_that_records_usage(input_tokens=1000, output_tokens=1000)
        budgeted = budget(tokens=10)(fn)
        result = budgeted("hello")
        assert result == "result:hello"

    def test_async_no_enforcement_without_run_context(self) -> None:
        fn = _make_async_function_that_records_usage(input_tokens=1000, output_tokens=1000)
        budgeted = budget(tokens=10)(fn)
        result = asyncio.run(budgeted("hello"))
        assert result == "result:hello"


# ---------------------------------------------------------------------------
# Error attributes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestBudgetLogging:
    def test_logs_warning_on_budget_exceeded(self, caplog: pytest.LogCaptureFixture) -> None:
        fn = _make_function_that_records_usage(input_tokens=100, output_tokens=100)
        budgeted = budget(tokens_per_call=50)(fn)

        with nh.run(StubExecutor()), caplog.at_level(logging.WARNING, logger="nighthawk.resilience"), pytest.raises(BudgetExceededError):
            budgeted("x")

        assert any("Budget exceeded" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Metadata preservation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Cost function tests (Phase 3)
# ---------------------------------------------------------------------------


def _dollar_per_thousand_tokens(usage: RunUsage) -> float:
    """Simple cost function: $0.01 per 1000 tokens."""
    return usage.total_tokens * 0.01 / 1000


class TestBudgetEstimate:
    def test_estimate_blocks_before_execution(self) -> None:
        call_count = 0

        def expensive(text: str) -> str:
            nonlocal call_count
            call_count += 1
            meter = get_current_usage_meter()
            if meter is not None:
                meter.record(RunUsage(input_tokens=5, output_tokens=5))
            return f"result:{text}"

        def estimate_usage(*_: object) -> RunUsage:
            return RunUsage(input_tokens=40, output_tokens=40)

        budgeted = budget(tokens=50, estimate_usage=estimate_usage)(expensive)

        with nh.run(StubExecutor()):
            meter = get_current_usage_meter()
            assert meter is not None
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
            meter = get_current_usage_meter()
            assert meter is not None
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
