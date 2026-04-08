from __future__ import annotations

import asyncio
import importlib.metadata
from collections.abc import Generator
from typing import Any, cast

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import nighthawk as nh
from nighthawk.errors import ExecutionError, NighthawkError
from nighthawk.runtime import scoping as runtime_scoping
from nighthawk.runtime.scoping import get_oversight
from nighthawk.runtime.step_contract import ReturnStepOutcome
from tests.execution.stub_executor import StubExecutor


@pytest.fixture
def step_span_exporter() -> Generator[InMemorySpanExporter, None, None]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    previous_tracer_provider = runtime_scoping._tracer
    runtime_scoping._tracer = tracer_provider.get_tracer("nighthawk", importlib.metadata.version("nighthawk-python"))
    try:
        yield span_exporter
    finally:
        runtime_scoping._tracer = previous_tracer_provider


def _get_finished_step_spans(step_span_exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span_data for span_data in step_span_exporter.get_finished_spans() if span_data.name == "nighthawk.step"]


def test_oversight_namespace_is_public() -> None:
    assert hasattr(nh, "oversight")
    assert not hasattr(nh, "governance")
    assert issubclass(nh.oversight.OversightRejectedError, NighthawkError)
    assert hasattr(nh.oversight, "Oversight")


def test_scope_oversight_inherits_and_can_clear() -> None:
    oversight = nh.oversight.Oversight()

    with nh.run(StubExecutor()):
        assert get_oversight() is None

        with nh.scope(oversight=oversight):
            assert get_oversight() is oversight

            with nh.scope():
                assert get_oversight() is oversight

            with nh.scope(oversight=None):
                assert get_oversight() is None

            assert get_oversight() is oversight


def test_scope_rejects_removed_governance_keyword() -> None:
    with (
        nh.run(StubExecutor()),
        pytest.raises(TypeError, match="governance"),
        nh.scope(governance=nh.oversight.Oversight()),  # type: ignore[call-arg]
    ):
        pass


def test_step_commit_reject_raises_without_failed_step_trace(step_span_exporter: InMemorySpanExporter) -> None:
    def reject_step(review: nh.oversight.StepCommitProposal) -> nh.oversight.Reject:
        _ = review
        return nh.oversight.Reject("host rejected step")

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=reject_step)):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 17}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        with pytest.raises(nh.oversight.OversightRejectedError, match="host rejected step"):
            natural_value_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    assert len(step_span.events) == 1
    oversight_event = step_span.events[0]
    assert oversight_event.name == "nighthawk.oversight.decision"
    oversight_attributes = dict(oversight_event.attributes or {})
    assert oversight_attributes["nighthawk.oversight.subject"] == "step_commit"
    assert oversight_attributes["nighthawk.oversight.verdict"] == "reject"
    assert oversight_attributes["nighthawk.oversight.reason"] == "host rejected step"
    assert str(oversight_attributes["step.id"]).startswith("test_governance:")
    assert step_span.status.status_code != StatusCode.ERROR


def test_step_commit_async_rewrite_updates_return_value() -> None:
    def rewrite_step(review: nh.oversight.StepCommitProposal) -> nh.oversight.Rewrite:
        assert review.proposed_binding_name_to_value["result"] == 11
        return nh.oversight.Rewrite(rewritten_binding_name_to_value={"result": 29})

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.natural_function
        async def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 11}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(natural_value_function()) == 29


def test_invalid_step_commit_decision_raises_nighthawk_error_and_records_failed_event(
    step_span_exporter: InMemorySpanExporter,
) -> None:
    def invalid_step_decision(_proposal: nh.oversight.StepCommitProposal) -> nh.oversight.StepCommitDecision:
        return cast(Any, "bad")

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=invalid_step_decision)):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 11}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        with pytest.raises(NighthawkError, match="must return Accept, Reject, or Rewrite"):
            natural_value_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    failed_event = next(event for event in step_span.events if event.name == "nighthawk.step.failed")
    failed_attributes = dict(failed_event.attributes or {})
    assert failed_attributes["nighthawk.step.error_kind"] == "NighthawkError"
    error_message = str(failed_attributes["nighthawk.step.error_message"])
    assert "must return Accept, Reject, or Rewrite" in error_message
    assert step_span.status.status_code == StatusCode.ERROR


def test_invalid_step_rewrite_flows_through_finalize_validation() -> None:
    def rewrite_step(review: nh.oversight.StepCommitProposal) -> nh.oversight.Rewrite:
        _ = review
        return nh.oversight.Rewrite(
            rewritten_step_outcome=ReturnStepOutcome(kind="return", return_expression="result"),
            rewritten_binding_name_to_value={"result": "not an int"},
        )

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.natural_function
        def natural_value_function() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 11}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        with pytest.raises(ExecutionError, match="Return value validation failed"):
            natural_value_function()


def test_empty_rewrite_is_rejected() -> None:
    with pytest.raises(ValueError, match="Rewrite must change"):
        nh.oversight.Rewrite()
