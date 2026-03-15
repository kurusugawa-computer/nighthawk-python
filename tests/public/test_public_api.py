import asyncio
from collections.abc import Generator

import pytest
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import NighthawkError
from nighthawk.runtime import scoping as runtime_scoping
from nighthawk.runtime.step_executor import AgentStepExecutor
from tests.execution.stub_executor import StubExecutor


class FakeMemory(BaseModel):
    n: int = 0


@pytest.fixture
def step_span_exporter() -> Generator[InMemorySpanExporter, None, None]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    previous_tracer_provider = runtime_scoping._tracer
    runtime_scoping._tracer = tracer_provider.get_tracer("nighthawk")
    try:
        yield span_exporter
    finally:
        runtime_scoping._tracer = previous_tracer_provider


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


def test_step_executor_replace_and_getter():
    step_executor = StubExecutor()

    with nh.run(
        step_executor,
    ):
        step_executor_value = nh.get_step_executor()
        assert step_executor_value == step_executor

    with pytest.raises(NighthawkError):
        nh.get_step_executor()


def test_scope_configuration_replaces_executor_configuration():
    class FakeAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            raise AssertionError

    configuration_1 = nh.StepExecutorConfiguration()
    configuration_2 = nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini")

    with nh.run(
        nh.AgentStepExecutor.from_agent(
            agent=FakeAgent(),
            configuration=configuration_1,
        )
    ):
        initial_step_executor = nh.get_step_executor()
        assert isinstance(initial_step_executor, AgentStepExecutor)
        assert initial_step_executor.configuration == configuration_1

        with nh.scope(
            step_executor_configuration=configuration_2,
        ):
            scoped_step_executor = nh.get_step_executor()
            assert isinstance(scoped_step_executor, AgentStepExecutor)
            assert scoped_step_executor.configuration == configuration_2

        restored_step_executor = nh.get_step_executor()
        assert isinstance(restored_step_executor, AgentStepExecutor)
        assert restored_step_executor.configuration == configuration_1


def test_scope_keeps_run_id_and_generates_new_scope_id() -> None:
    with nh.run(
        StubExecutor(),
        run_id="run-test",
    ):
        parent_execution_context = nh.get_execution_context()
        with nh.scope():
            nested_execution_context = nh.get_execution_context()

        assert parent_execution_context.run_id == "run-test"
        assert nested_execution_context.run_id == "run-test"
        assert parent_execution_context.scope_id != nested_execution_context.scope_id


def test_scope_requires_existing_step_executor():
    with pytest.raises(NighthawkError), nh.scope():
        pass


def test_run_configuration_model_default_applies():
    configuration = nh.StepExecutorConfiguration()
    assert configuration.model == "openai-responses:gpt-5-nano"


def test_run_configuration_model_requires_provider_model_format():
    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(model="openai-responses")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(model=":gpt-5-nano")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(model="openai-responses:")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(model="openai-responses:gpt-5-nano:extra")


def test_agent_step_executor_constructor_supports_standard_path_with_agent() -> None:
    class FakeAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            raise AssertionError

    step_executor = nh.AgentStepExecutor(agent=FakeAgent())
    assert step_executor.configuration.model == "openai-responses:gpt-5-nano"


def test_decorated_function_requires_step_executor():
    @nh.natural_function
    def f(x: int):
        f"""natural
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """  # noqa: B021
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        f(1)


def test_async_decorated_function_requires_step_executor():
    @nh.natural_function
    async def f(x: int):
        f"""natural
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """  # noqa: B021
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        asyncio.run(f(1))


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
            {"step_outcome": {"kind": "return", "return_reference_path": "result"}, "bindings": {"result": 11}}
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


def test_step_span_keeps_step_id_attribute_format(step_span_exporter: InMemorySpanExporter) -> None:
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
    step_id = _require_non_empty_text(_require_attribute_value(step_span, "step.id"))
    module_name, line_text = step_id.split(":", 1)
    assert module_name == "tests.public.test_public_api"
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
            {"step_outcome": {"kind": "return", "return_reference_path": "result"}, "bindings": {"result": 5}}
            """
            return 0

        with pytest.raises(nh.ExecutionError, match="not allowed"):
            natural_value_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    failed_event_attribute_by_name = {
        event.name: dict(event.attributes or {})
        for event in step_span.events
        if event.name == "nighthawk.step.failed"
    }
    assert failed_event_attribute_by_name == {
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
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context,
            binding_names: list[str],
            allowed_step_kinds: tuple[str, ...],
        ):
            _ = (processed_natural_program, step_context, binding_names, allowed_step_kinds)
            raise CustomExecutionError("ignored")

    with nh.run(FailingExecutor()):

        @nh.natural_function
        def natural_failure_function() -> None:
            """natural
            fail
            """

        with pytest.raises(CustomExecutionError):
            natural_failure_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    failed_event_attribute_by_name = {
        event.name: dict(event.attributes or {})
        for event in step_span.events
        if event.name == "nighthawk.step.failed"
    }
    assert failed_event_attribute_by_name == {
        "nighthawk.step.failed": {
            "nighthawk.step.error_kind": "CustomExecutionError",
            "nighthawk.step.error_message": "custom string message",
        }
    }
    assert step_span.status.status_code == StatusCode.ERROR
    assert "exception" in {event.name for event in step_span.events}
    assert "nighthawk.step.failed" in {event.name for event in step_span.events}


def test_step_trace_symbols_are_removed_from_public_api() -> None:
    assert not hasattr(nh, "get_step_traces")
    assert not hasattr(nh, "StepTrace")
    assert not hasattr(nh, "StepTraceError")


def test_step_span_attributes_include_run_scope_and_step_identity(step_span_exporter: InMemorySpanExporter) -> None:
    with nh.run(StubExecutor(), run_id="trace-run"):
        expected_scope_id = nh.get_execution_context().scope_id

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
    _require_non_empty_text(_require_attribute_value(step_span, "step.id"))


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
    assert _require_attribute_key_set(step_span) == {"run.id", "scope.id", "step.id"}
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
            {"step_outcome": {"kind": "return", "return_reference_path": "result"}, "bindings": {"result": 5}}
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
