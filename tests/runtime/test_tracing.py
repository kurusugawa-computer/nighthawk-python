import pytest
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import nighthawk as nh
from nighthawk.runtime import StepCandidate, StepRequest
from tests.support.executors import StubExecutor


def _get_finished_step_spans(step_span_exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span_data for span_data in step_span_exporter.get_finished_spans() if span_data.name == "nighthawk.step"]


def _require_attribute_value(step_span: ReadableSpan, key: str) -> object:
    attributes = step_span.attributes
    assert attributes is not None
    assert key in attributes
    return attributes[key]


def _require_attribute_key_set(step_span: ReadableSpan) -> set[str]:
    attributes = step_span.attributes
    assert attributes is not None
    return set(attributes.keys())


def _require_event_attribute_key_set(step_event: Event) -> set[str]:
    attributes = step_event.attributes
    assert attributes is not None
    return set(attributes.keys())


def _require_non_empty_text(value: object) -> str:
    assert isinstance(value, str)
    assert value
    return value


def test_step_span_records_completed_event_for_pass_outcome(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 17}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert natural_value_function() == 17

    step_spans = _get_finished_step_spans(step_span_exporter)
    assert len(step_spans) == 1
    step_events = [(event.name, dict(event.attributes or {})) for event in step_spans[0].events]
    assert step_events == [
        (
            "nighthawk.step.completed",
            {"nighthawk.step.outcome_kind": "pass"},
        )
    ]


def test_step_span_records_completed_event_for_return_outcome(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 11}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert natural_value_function() == 11

    step_spans = _get_finished_step_spans(step_span_exporter)
    assert len(step_spans) == 1
    step_events = [(event.name, dict(event.attributes or {})) for event in step_spans[0].events]
    assert step_events == [
        (
            "nighthawk.step.completed",
            {"nighthawk.step.outcome_kind": "return"},
        )
    ]


def test_step_span_keeps_source_location_attribute_format(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 23}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert natural_value_function() == 23

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    source_location = _require_non_empty_text(_require_attribute_value(step_span, "step.source_location"))
    module_name, line_text = source_location.split(":", 1)
    assert module_name == __name__
    assert line_text.isdigit()
    assert int(line_text) > 0


def test_step_span_records_raise_event_without_error_status(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def natural_raise_function() -> None:
            """natural
            <ValueError>
            {"step_outcome": {"kind": "raise", "raise_message": "expected", "raise_error_type": "ValueError"}, "bindings": {}}
            """

        with pytest.raises(ValueError, match="expected"):
            natural_raise_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    step_events = [(event.name, dict(event.attributes or {})) for event in step_span.events]
    assert step_events == [
        (
            "nighthawk.step.raised",
            {
                "nighthawk.step.outcome_kind": "raise",
                "nighthawk.step.raise_message": "expected",
                "nighthawk.step.raise_error_type": "ValueError",
            },
        )
    ]
    assert step_span.status.status_code != StatusCode.ERROR


def test_step_span_records_failure_event_for_execution_error(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            ---
            deny:
              - return
            ---
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 5}}
            """
            return 0

        with pytest.raises(nh.ExecutionError, match="not allowed"):
            natural_value_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    event_name_to_attribute = {event.name: dict(event.attributes or {}) for event in step_span.events if event.name == "nighthawk.step.failed"}
    assert event_name_to_attribute == {
        "nighthawk.step.failed": {
            "nighthawk.step.error_kind": "ExecutionError",
            "nighthawk.step.error_message": "Step 'return' is not allowed for this step. Allowed kinds: ('pass', 'raise')",
        }
    }
    assert step_span.status.status_code == StatusCode.ERROR
    assert "exception" in {event.name for event in step_span.events}
    assert "nighthawk.step.failed" in {event.name for event in step_span.events}


def test_step_span_records_failure_event_for_executor_exception(step_span_exporter: InMemorySpanExporter) -> None:
    class CustomExecutionError(nh.ExecutionError):
        def __str__(self) -> str:
            return "custom string message"

    class FailingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_step_kinds = request.allowed_step_kinds
            _ = (processed_natural_program, step_context, binding_names, allowed_step_kinds)
            raise CustomExecutionError("ignored")

    with nh.run(FailingExecutor()):

        @nh.natural_function
        def natural_failure_function() -> None:
            """natural
            fail
            """

        with pytest.raises(nh.ExecutionError) as caught:
            natural_failure_function()
        assert isinstance(caught.value.__cause__, CustomExecutionError)

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    event_name_to_attribute = {event.name: dict(event.attributes or {}) for event in step_span.events if event.name == "nighthawk.step.failed"}
    assert event_name_to_attribute == {
        "nighthawk.step.failed": {
            "nighthawk.step.error_kind": "CustomExecutionError",
            "nighthawk.step.error_message": "custom string message",
        }
    }
    assert step_span.status.status_code == StatusCode.ERROR
    assert "exception" in {event.name for event in step_span.events}
    assert "nighthawk.step.failed" in {event.name for event in step_span.events}


def test_step_span_attributes_include_run_scope_and_step_identity(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor(), run_id="trace-run"):
        expected_scope_id = nh.get_execution_reference().scope_id

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 31}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert natural_value_function() == 31

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    assert _require_attribute_value(step_span, "run.id") == "trace-run"
    assert _require_attribute_value(step_span, "scope.id") == expected_scope_id
    _require_non_empty_text(_require_attribute_value(step_span, "step.execution.id"))


def test_step_span_event_structure_is_compact(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 111}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert natural_value_function() == 111

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    step_event = step_span.events[0]
    assert step_event.name == "nighthawk.step.completed"
    assert _require_event_attribute_key_set(step_event) == {"nighthawk.step.outcome_kind"}
    assert _require_attribute_key_set(step_span) == {"run.id", "scope.id", "step.execution.id", "step.source_location"}
    assert len(step_span.events) == 1


def test_step_span_failure_event_structure_is_compact(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            ---
            deny:
              - return
            ---
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 5}}
            """
            return 0

        with pytest.raises(nh.ExecutionError, match="not allowed"):
            natural_value_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    failure_event = step_span.events[0]
    assert failure_event.name == "nighthawk.step.failed"
    assert set((failure_event.attributes or {}).keys()) == {
        "nighthawk.step.error_kind",
        "nighthawk.step.error_message",
    }
    assert step_span.status.status_code == StatusCode.ERROR
    exception_event_name_set = {event.name for event in step_span.events}
    assert "exception" in exception_event_name_set
    assert "nighthawk.step.failed" in exception_event_name_set
    assert len(step_span.events) == 2


def test_run_span_has_only_run_id_attribute(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor(), run_id="attr-test"):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 42}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert natural_value_function() == 42

    run_span_list = [s for s in step_span_exporter.get_finished_spans() if s.name == "nighthawk.run"]
    assert len(run_span_list) == 1
    run_span = run_span_list[0]
    assert _require_attribute_key_set(run_span) == {"run.id"}
    assert _require_attribute_value(run_span, "run.id") == "attr-test"
