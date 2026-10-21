from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, Any, cast

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import AfterValidator, BaseModel

import nighthawk as nh
from nighthawk.errors import ExecutionError, NighthawkError
from nighthawk.runtime import Pass, StepCandidate, StepRequest
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.model_candidate import resolve_model_candidate
from nighthawk.runtime.model_outcome import ModelRaise
from nighthawk.runtime.scoping import get_oversight
from tests.support.executors import StubExecutor


class GovernanceResultModel(BaseModel):
    value: int


def _get_finished_step_spans(step_span_exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span_data for span_data in step_span_exporter.get_finished_spans() if span_data.name == "nighthawk.step"]


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


def test_step_commit_reject_records_failed_step_trace(step_span_exporter: InMemorySpanExporter) -> None:
    def reject_step(review: nh.oversight.StepCommit) -> nh.oversight.AbortStep:
        _ = review
        return nh.oversight.AbortStep("host rejected step")

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=reject_step)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 17}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        with pytest.raises(nh.oversight.StepAbortedError, match="host rejected step"):
            natural_value_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    assert [event.name for event in step_span.events] == ["nighthawk.oversight.decision", "nighthawk.step.failed", "exception"]
    oversight_event = step_span.events[0]
    assert oversight_event.name == "nighthawk.oversight.decision"
    oversight_attributes = dict(oversight_event.attributes or {})
    assert oversight_attributes["nighthawk.oversight.subject"] == "step_commit"
    assert oversight_attributes["nighthawk.oversight.verdict"] == "abort"
    assert oversight_attributes["nighthawk.oversight.reason"] == "host rejected step"
    assert str(oversight_attributes["step.source_location"]).startswith(f"{__name__}:")
    assert step_span.status.status_code == StatusCode.ERROR


def test_step_commit_async_rewrite_updates_return_value() -> None:
    def rewrite_step(review: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        assert review.binding_name_to_value["result"] == 11
        return nh.oversight.Rewrite(binding_name_to_value={"result": 29})

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.function
        async def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 11}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(natural_value_function()) == 29


def test_invalid_step_commit_decision_raises_nighthawk_error_and_records_failed_event(
    step_span_exporter: InMemorySpanExporter,
) -> None:
    def invalid_step_decision(_proposal: nh.oversight.StepCommit) -> nh.oversight.StepCommitDecision:
        return cast(Any, "bad")

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=invalid_step_decision)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 11}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        with pytest.raises(NighthawkError, match="must return Accept, Rewrite, or AbortStep"):
            natural_value_function()

    step_span = _get_finished_step_spans(step_span_exporter)[0]
    failed_event = next(event for event in step_span.events if event.name == "nighthawk.step.failed")
    failed_attributes = dict(failed_event.attributes or {})
    assert failed_attributes["nighthawk.step.error_kind"] == "NighthawkError"
    error_message = str(failed_attributes["nighthawk.step.error_message"])
    assert "must return Accept, Rewrite, or AbortStep" in error_message
    assert step_span.status.status_code == StatusCode.ERROR


def test_invalid_step_rewrite_flows_through_finalize_validation() -> None:
    def rewrite_step(review: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        _ = review
        return nh.oversight.Rewrite(
            outcome=nh.oversight.Return(value="not an int"),
            binding_name_to_value={"result": "not an int"},
        )

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 11}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        with pytest.raises(ExecutionError, match="Return value validation failed"):
            natural_value_function()


def test_step_commit_rewrite_is_coerced_by_finalize_validation() -> None:
    def rewrite_step(review: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        assert review.binding_name_to_value["result"] == GovernanceResultModel(value=1)
        return nh.oversight.Rewrite(binding_name_to_value={"result": {"value": "29"}})

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": {"value": 1}}}
            """
            result: GovernanceResultModel
            return result.value  # noqa: F821  # pyright: ignore[reportUndefinedVariable, reportUnboundVariable, reportAttributeAccessIssue]

        assert natural_value_function() == 29


def test_empty_rewrite_is_rejected() -> None:
    with pytest.raises(ValueError, match="Rewrite must change"):
        nh.oversight.Rewrite()


def test_step_commit_receives_validated_bindings_and_return_value() -> None:
    observed_commits: list[nh.oversight.StepCommit] = []

    def observe(step_commit: nh.oversight.StepCommit) -> nh.oversight.Accept:
        observed_commits.append(step_commit)
        return nh.oversight.Accept()

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=observe)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "int(result) + 1"}, "bindings": {"result": "41"}}
            """
            result: int = 0
            return result

        assert natural_value_function() == 42

    assert len(observed_commits) == 1
    step_commit = observed_commits[0]
    assert step_commit.binding_name_to_value["result"] == 41
    assert isinstance(step_commit.outcome, nh.oversight.Return)
    assert step_commit.outcome.value == 42


def test_step_commit_pass_has_no_return_value() -> None:
    observed_return_values: list[object] = []

    def observe(step_commit: nh.oversight.StepCommit) -> nh.oversight.Accept:
        observed_return_values.append(step_commit.outcome)
        return nh.oversight.Accept()

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=observe)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 3}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert natural_value_function() == 3

    assert observed_return_values == [nh.oversight.Pass()]


def test_invalid_executor_output_fails_before_oversight_is_consulted() -> None:
    hook_invocation_count = 0

    def observe(step_commit: nh.oversight.StepCommit) -> nh.oversight.Accept:
        nonlocal hook_invocation_count
        hook_invocation_count += 1
        return nh.oversight.Accept()

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=observe)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": "not an int"}}
            """
            result: int = 0
            return result

        with pytest.raises(ExecutionError, match="failed validation"):
            natural_value_function()

    assert hook_invocation_count == 0


def test_rewrite_return_value_is_validated_against_return_annotation() -> None:
    def rewrite_step(step_commit: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        assert isinstance(step_commit.outcome, nh.oversight.Return)
        assert step_commit.outcome.value == 5
        return nh.oversight.Rewrite(outcome=nh.runtime.Return("7"))

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 5}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert natural_value_function() == 7


def test_rewrite_return_value_type_mismatch_raises_execution_error() -> None:
    def rewrite_step(step_commit: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        _ = step_commit
        return nh.oversight.Rewrite(outcome=nh.runtime.Return("not an int"))

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 5}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        with pytest.raises(ExecutionError, match="Return value validation failed"):
            natural_value_function()


def test_rewrite_bindings_with_invalid_value_raises_execution_error() -> None:
    def rewrite_step(step_commit: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        _ = step_commit
        return nh.oversight.Rewrite(binding_name_to_value={"result": "not an int"})

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 5}}
            """
            result: int = 0
            return result

        with pytest.raises(ExecutionError, match="failed validation"):
            natural_value_function()


def test_rewrite_to_disallowed_outcome_kind_raises_execution_error() -> None:
    def rewrite_step(step_commit: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        _ = step_commit
        return nh.oversight.Rewrite(outcome=nh.oversight.Return(value=5))

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=rewrite_step)):

        @nh.function
        def natural_value_function() -> int:
            """execute
            ---
            deny:
              - return
            ---
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 5}}
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        with pytest.raises(ExecutionError, match="not allowed"):
            natural_value_function()


def test_explicit_none_return_and_resolved_kind_change() -> None:
    @nh.function
    def optional() -> int | None:
        """execute
        {"step_outcome": {"kind": "return", "return_expression": "5"}, "bindings": {}}
        """
        return 0

    @nh.function
    def required() -> int:
        """execute
        {"step_outcome": {"kind": "return", "return_expression": "5"}, "bindings": {}}
        """
        return 0

    @nh.function
    def passing() -> int:
        """execute
        {"step_outcome": {"kind": "pass"}, "bindings": {}}
        """
        return 0

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(outcome=nh.runtime.Return(None)))),
    ):
        assert optional() is None
        with pytest.raises(nh.ExecutionError, match="Return value validation failed"):
            required()
        with pytest.raises(nh.ExecutionError, match="Return value validation failed"):
            passing()
    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(outcome=nh.oversight.Return(7)))),
    ):
        assert passing() == 7


def test_accept_does_not_repeat_validators_and_replacement_does() -> None:
    calls: list[int] = []

    def validate(value: int) -> int:
        calls.append(value)
        return value + 1

    Counted = Annotated[int, AfterValidator(validate)]  # noqa: N806

    @nh.function
    def workflow() -> Counted:  # type: ignore[valid-type]
        nonlocal Counted
        """execute
        <Counted>
        {"step_outcome": {"kind": "return", "return_expression": "4"}, "bindings": {}}
        """
        return 0

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Accept())):
        assert workflow() == 5
    assert calls == [4]
    calls.clear()
    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(outcome=nh.runtime.Return(8)))),
    ):
        assert workflow() == 9
    assert calls == [4, 8]


def test_reference_views_preserve_validated_values_and_freeze_only_mapping() -> None:
    class Record(BaseModel):
        value: int

    values = [1]
    record = Record(value=2)
    unique = object()
    captured: list[nh.oversight.StepCommit] = []

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return StepCandidate(outcome=Pass(), binding_name_to_value={"values": values, "record": record, "unique": unique})

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.Accept:
        captured.append(commit)
        assert commit.binding_name_to_value["values"] is values
        assert commit.binding_name_to_value["record"] is record
        assert commit.binding_name_to_value["unique"] is unique
        with pytest.raises(TypeError):
            commit.binding_name_to_value["values"] = []  # type: ignore[index]
        return nh.oversight.Accept()

    @nh.function
    def workflow() -> tuple[object, object, object]:
        """execute
        <:values> <:record> <:unique>
        Produce values.
        """
        return values, record, unique

    with nh.run(Executor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=inspect)):
        result = workflow()
    assert result[0] is values and result[1] is record and result[2] is unique
    assert len(captured) == 1


def test_raise_to_pass_does_not_resurrect_unvalidated_writes() -> None:
    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            request.step_context.record_assignment("result", "invalid")
            return run_coroutine_synchronously(lambda: resolve_model_candidate(request, ModelRaise(kind="raise", raise_message="failed")))

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        assert commit.binding_name_to_value == {}
        assert isinstance(commit.outcome, nh.oversight.Raise)
        return nh.oversight.Rewrite(outcome=nh.oversight.Pass())

    @nh.function
    def workflow() -> int:
        result: int = 3
        """execute
        <:result>
        Raise an error.
        """
        return result

    with nh.run(Executor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=inspect)):
        assert workflow() == 3


def test_raise_rewrite_validates_replacement_without_constructing_original_exception() -> None:
    constructed: list[str] = []

    class OriginalError(Exception):
        def __init__(self, message: str) -> None:
            constructed.append(message)
            super().__init__(message)

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return run_coroutine_synchronously(
                lambda: resolve_model_candidate(request, ModelRaise(kind="raise", raise_message="original", raise_error_type="OriginalError"), {})
            )

    @nh.function
    def workflow() -> int:
        nonlocal OriginalError
        """execute
        <OriginalError>
        Raise an error.
        """
        return 7

    with nh.run(Executor()):
        with (
            nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Accept())),
            pytest.raises(OriginalError, match="original"),
        ):
            workflow()
        assert constructed == ["original"]
        constructed.clear()
        with nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(outcome=nh.oversight.Pass()))):
            assert workflow() == 7
        with (
            nh.scope(
                oversight=nh.oversight.Oversight(
                    inspect_step_commit=lambda commit: nh.oversight.Rewrite(outcome=nh.oversight.Raise("replacement", int))  # type: ignore[arg-type]
                )
            ),
            pytest.raises(nh.ExecutionError, match="resolved exception class"),
        ):
            workflow()
    assert constructed == []


@pytest.mark.parametrize(
    "construct",
    [
        lambda: nh.oversight.Rewrite(),
        lambda: nh.oversight.Rewrite(outcome=None),  # type: ignore[arg-type]
        lambda: nh.oversight.Rewrite(binding_name_to_value=None),  # type: ignore[arg-type]
    ],
)
def test_invalid_rewrite_shapes(construct: Callable[[], object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        construct()


def test_invalid_replacement_never_partially_commits_or_reinspects() -> None:
    calls: list[nh.oversight.StepCommit] = []

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        calls.append(commit)
        return nh.oversight.Rewrite(binding_name_to_value={"first": 99, "second": "invalid"})

    @nh.function
    def workflow() -> tuple[int, int]:
        first: int = 1
        second: int = 2
        try:
            """execute
            <:first> <:second>
            {"step_outcome": {"kind": "pass"}, "bindings": {"first": 3, "second": 4}}
            """
        except nh.ExecutionError:
            return first, second
        raise AssertionError("Expected validation failure")

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=inspect)):
        assert workflow() == (1, 2)
    assert len(calls) == 1


def test_undeclared_rewrite_and_raise_with_writes_are_rejected() -> None:
    @nh.function
    def workflow() -> None:
        """execute
        {"step_outcome": {"kind": "pass"}, "bindings": {}}
        """

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(binding_name_to_value={"unknown": 1}))),
        pytest.raises(nh.NighthawkError),
    ):
        workflow()
    with pytest.raises(ValueError, match="Raise cannot include output bindings"):
        nh.oversight.Rewrite(outcome=nh.oversight.Raise("failed"), binding_name_to_value={"unknown": 1})
