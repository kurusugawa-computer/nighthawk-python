from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import AfterValidator, BaseModel

import nighthawk as nh
from nighthawk.lifecycle import FailureStage, StepCompleted, StepDeliveryError, StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepRaised
from nighthawk.runtime import scoping
from nighthawk.runtime.step_contract import PassStepOutcome, StepKind, StepOutcome
from nighthawk.testing import ScriptedExecutor, StepResponse
from tests.execution.stub_executor import StubExecutor


class NaturalExecutionError(Exception):
    def __init__(self, event: StepFailed | StepRaised) -> None:
        self.event = event
        super().__init__(f"Host execution failed: {event.execution_reference.step_execution_id}")


@dataclass
class Ledger:
    execution_id_to_event: dict[str, StepFinished] = field(default_factory=dict)

    def finished(self, event: StepFinished) -> None:
        execution_id = event.execution_reference.step_execution_id
        assert execution_id is not None and execution_id not in self.execution_id_to_event
        self.execution_id_to_event[execution_id] = event
        if isinstance(event, (StepFailed, StepRaised)):
            raise NaturalExecutionError(event)


@pytest.mark.parametrize(
    "scenario,expected_stage",
    [
        ("binding", "binding_validation"),
        ("evaluation", "return_evaluation"),
        ("validation", "return_validation"),
        ("executor", "executor"),
        ("rewrite", "rewrite_validation"),
        ("reject", "oversight_rejection"),
        ("inspector", "commit_inspection"),
        ("return_inspector", "return_inspection"),
        ("outcome", "outcome_validation"),
        ("undeclared", "binding_validation"),
        ("raise_type", "raise_resolution"),
    ],
)
def test_host_exception_references_single_ledger_event(scenario: str, expected_stage: FailureStage) -> None:
    ledger = Ledger()

    class Executor:
        def run_step(
            self, *, processed_natural_program: str, step_context: nh.StepContext, binding_names: list[str], allowed_step_kinds: tuple[StepKind, ...]
        ) -> tuple[StepOutcome, dict[str, object]]:
            if scenario == "executor":
                raise ValueError("executor failed")
            if scenario == "undeclared":
                return PassStepOutcome(kind="pass"), {"unknown": 5}
            return StubExecutor().run_step(
                processed_natural_program=processed_natural_program,
                step_context=step_context,
                binding_names=binding_names,
                allowed_step_kinds=allowed_step_kinds,
            )

    def inspect_commit(commit: nh.oversight.StepCommit) -> nh.oversight.StepCommitDecision:
        if scenario == "rewrite":
            return nh.oversight.Rewrite(binding_name_to_value={"value": "bad"})
        if scenario == "reject":
            return nh.oversight.Reject("rejected")
        if scenario == "inspector":
            raise ValueError("inspection failed")
        return nh.oversight.Accept()

    def inspect_expression(request: nh.oversight.ReturnExpression) -> nh.oversight.Accept:
        if scenario == "return_inspector":
            raise ValueError("return inspection failed")
        return nh.oversight.Accept()

    program = json.dumps(
        {
            "step_outcome": {
                "kind": "return",
                "return_expression": "1/0" if scenario == "evaluation" else repr("bad") if scenario == "validation" else "7",
            },
            "bindings": {"value": "bad" if scenario == "binding" else 3},
        }
    )

    @nh.natural_function
    def workflow() -> int:
        value: int = 0
        f"""natural
        <:value>
        {program}
        """
        return value

    @nh.natural_function
    def disallowed() -> None:
        """natural
        {"step_outcome": {"kind": "break"}, "bindings": {}}
        """

    @nh.natural_function
    def invalid_raise() -> None:
        """natural
        {"step_outcome": {"kind": "raise", "raise_message": "failed", "raise_error_type": "MissingException"}, "bindings": {}}
        """

    with (
        nh.run(Executor()),
        nh.scope(
            lifecycle=StepLifecycle(ledger.finished),
            oversight=nh.oversight.Oversight(
                inspect_step_commit=inspect_commit,
                inspect_return_expression=inspect_expression,
            ),
        ),
        pytest.raises(NaturalExecutionError) as caught,
    ):
        if scenario == "outcome":
            disallowed()
        elif scenario == "raise_type":
            invalid_raise()
        else:
            workflow()
    assert len(ledger.execution_id_to_event) == 1
    event = next(iter(ledger.execution_id_to_event.values()))
    assert caught.value.event is event
    assert isinstance(event, StepFailed) and event.failure_stage == expected_stage
    assert caught.value.__cause__ is event.original_exception
    assert event.assigned_binding_name_to_value == {}


def test_inner_python_handler_does_not_hide_step_failure() -> None:
    ledger = Ledger()

    @nh.natural_function
    def workflow() -> NaturalExecutionError | None:
        try:
            """natural
            {"step_outcome": {"kind": "return", "return_expression": "1/0"}, "bindings": {}}
            """
        except NaturalExecutionError as exception:
            return exception
        return None

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(ledger.finished)):
        error = workflow()
    assert isinstance(error, NaturalExecutionError)
    assert list(ledger.execution_id_to_event.values()) == [error.event]


@pytest.mark.parametrize("tracing", [False, True])
def test_return_await_failure_is_recorded_and_translated(tracing: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = Ledger()
    failure = ValueError("await failed")
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    if tracing:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(scoping, "_tracer", provider.get_tracer(__name__))

    async def calculate() -> int:
        await asyncio.sleep(0)
        raise failure

    @nh.natural_function
    async def workflow() -> int:
        """natural
        <calculate>
        {"step_outcome": {"kind": "return", "return_expression": "calculate()"}, "bindings": {}}
        """
        return 0

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(ledger.finished)), pytest.raises(NaturalExecutionError) as caught:
        asyncio.run(workflow())
    event = caught.value.event
    assert list(ledger.execution_id_to_event.values()) == [event]
    assert isinstance(event, StepFailed) and event.failure_stage == "return_await"
    assert event.original_exception is failure is caught.value.__cause__
    if tracing:
        step_span = next(span for span in exporter.get_finished_spans() if span.name == "nighthawk.step")
        assert step_span.status.status_code == StatusCode.ERROR
        assert [event.name for event in step_span.events].count("nighthawk.step.failed") == 1
        assert "exception" in {event.name for event in step_span.events}
        assert "nighthawk.step.completed" not in {event.name for event in step_span.events}
    provider.shutdown()


def test_success_ledger_preserves_identity_and_validates_once() -> None:
    ledger = Ledger()
    validations: list[object] = []

    class Record(BaseModel):
        value: int

    original = Record(value=3)

    def validate(value: Record) -> Record:
        validations.append(value)
        return value

    Counted = Annotated[Record, AfterValidator(validate)]  # noqa: N806

    @nh.natural_function
    def workflow() -> Counted:  # type: ignore[valid-type]
        value: Counted = original  # pyright: ignore[reportInvalidTypeForm]
        """natural
        <Counted> <:value>
        Finish.
        """
        return value

    executor = ScriptedExecutor([StepResponse(bindings={"value": original})])
    with nh.run(executor), nh.scope(lifecycle=StepLifecycle(ledger.finished)):
        assert workflow() is original
    event = next(iter(ledger.execution_id_to_event.values()))
    assert isinstance(event, StepCompleted)
    assert event.assigned_binding_name_to_value["value"] is original
    assert validations == [original]
    assert isinstance(event.assigned_binding_name_to_value, Mapping)
    with pytest.raises(TypeError):
        event.assigned_binding_name_to_value["value"] = 0  # type: ignore[index]


@pytest.mark.parametrize("append_first", [False, True])
def test_delivery_failure_does_not_retry_or_invent_persistence(append_first: bool) -> None:
    ledger = Ledger()
    attempts: list[StepFinished] = []
    storage_error = OSError("storage unavailable")

    def finished(event: StepFinished) -> None:
        attempts.append(event)
        if append_first:
            ledger.finished(event)
        raise StepDeliveryError(event.execution_reference, storage_error)

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    executor = ScriptedExecutor()
    with nh.run(executor), nh.scope(lifecycle=StepLifecycle(finished)), pytest.raises(StepDeliveryError) as caught:
        workflow()
    assert len(attempts) == len(executor.calls) == 1
    assert len(ledger.execution_id_to_event) == int(append_first)
    assert caught.value.execution_reference == attempts[0].execution_reference
    assert caught.value.delivery_cause is storage_error is caught.value.__cause__


def test_preparation_failure_has_identity_and_unknown_fields() -> None:
    ledger = Ledger()

    @nh.natural_function
    def workflow() -> None:
        preparation_text = "Finish."
        f"""natural
        ---
        deny: [not_an_outcome]
        ---
        {preparation_text}
        """

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(ledger.finished)), pytest.raises(NaturalExecutionError) as caught:
        workflow()
    event = caught.value.event
    assert isinstance(event, StepFailed) and event.failure_stage == "preparation"
    assert event.allowed_step_kinds is None
    assert event.input_binding_name_to_value is None
    assert event.processed_natural_program is None
    assert event.execution_reference.step_execution_id is not None


def test_dsl_raise_translates_from_prepared_exception() -> None:
    ledger = Ledger()

    @nh.natural_function
    def workflow() -> None:
        """natural
        <ValueError>
        {"step_outcome": {"kind": "raise", "raise_message": "domain", "raise_error_type": "ValueError"}, "bindings": {}}
        """

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(ledger.finished)), pytest.raises(NaturalExecutionError) as caught:
        workflow()
    event = caught.value.event
    assert isinstance(event, StepRaised)
    assert isinstance(event.exception, ValueError)
    assert caught.value.__cause__ is event.exception
    assert list(ledger.execution_id_to_event.values()) == [event]


def test_cancellation_is_stored_without_host_translation() -> None:
    ledger = Ledger()
    interruption = asyncio.CancelledError()

    async def cancelled() -> int:
        raise interruption

    @nh.natural_function
    async def workflow() -> int:
        """natural
        <cancelled>
        {"step_outcome": {"kind": "return", "return_expression": "cancelled()"}, "bindings": {}}
        """
        return 0

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(ledger.finished)), pytest.raises(asyncio.CancelledError):
        asyncio.run(workflow())
    event = next(iter(ledger.execution_id_to_event.values()))
    assert isinstance(event, StepInterrupted) and event.original_exception is interruption
