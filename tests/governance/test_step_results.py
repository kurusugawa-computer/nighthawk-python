from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, Any

import pytest
from pydantic import AfterValidator, BaseModel

import nighthawk as nh
from nighthawk.runtime.step_contract import PassStepOutcome, RaiseStepOutcome
from tests.execution.stub_executor import StubExecutor


def test_explicit_none_return_and_resolved_kind_change() -> None:
    @nh.natural_function
    def optional() -> int | None:
        """natural
        {"step_outcome": {"kind": "return", "return_expression": "5"}, "bindings": {}}
        """
        return 0

    @nh.natural_function
    def required() -> int:
        """natural
        {"step_outcome": {"kind": "return", "return_expression": "5"}, "bindings": {}}
        """
        return 0

    @nh.natural_function
    def passing() -> int:
        """natural
        {"step_outcome": {"kind": "pass"}, "bindings": {}}
        """
        return 0

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(return_value=None))),
    ):
        assert optional() is None
        with pytest.raises(nh.ExecutionError, match="Return value validation failed"):
            required()
        with pytest.raises(nh.NighthawkError, match="existing Return"):
            passing()
    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(outcome=nh.oversight.Return(7)))),
    ):
        assert passing() == 7


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("decision", [nh.oversight.Accept(), nh.oversight.Rewrite(binding_name_to_value={"result": 99})])
def test_return_expression_runs_once_and_keeps_resolved_value(asynchronous: bool, decision: nh.oversight.StepCommitDecision) -> None:
    calls: list[str] = []

    def calculate() -> int:
        calls.append("calculated")
        return 12

    async def calculate_async() -> int:
        return calculate()

    @nh.natural_function
    def synchronous() -> int:
        """natural
        <calculate> <:result>
        {"step_outcome": {"kind": "return", "return_expression": "calculate() + result"}, "bindings": {"result": 1}}
        """
        return 0

    @nh.natural_function
    async def asynchronous_function() -> int:
        """natural
        <calculate_async> <:result>
        {"step_outcome": {"kind": "return", "return_expression": "(await calculate_async()) + result"}, "bindings": {"result": 1}}
        """
        return 0

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: decision)):
        assert (asyncio.run(asynchronous_function()) if asynchronous else synchronous()) == 13
    assert calls == ["calculated"]


def test_accept_does_not_repeat_validators_and_replacement_does() -> None:
    calls: list[int] = []

    def validate(value: int) -> int:
        calls.append(value)
        return value + 1

    Counted = Annotated[int, AfterValidator(validate)]  # noqa: N806

    @nh.natural_function
    def workflow() -> Counted:  # type: ignore[valid-type]
        """natural
        <Counted>
        {"step_outcome": {"kind": "return", "return_expression": "4"}, "bindings": {}}
        """
        return 0

    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Accept())):
        assert workflow() == 5
    assert calls == [4]
    calls.clear()
    with nh.run(StubExecutor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit: nh.oversight.Rewrite(return_value=8))):
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
        def run_step(self, **arguments: Any) -> tuple[PassStepOutcome, dict[str, object]]:
            return PassStepOutcome(kind="pass"), {"values": values, "record": record, "unique": unique}

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.Accept:
        captured.append(commit)
        assert commit.binding_name_to_value["values"] is values
        assert commit.binding_name_to_value["record"] is record
        assert commit.binding_name_to_value["unique"] is unique
        with pytest.raises(TypeError):
            commit.binding_name_to_value["values"] = []  # type: ignore[index]
        return nh.oversight.Accept()

    @nh.natural_function
    def workflow() -> tuple[object, object, object]:
        """natural
        <:values> <:record> <:unique>
        Produce values.
        """
        return values, record, unique

    with nh.run(Executor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=inspect)):
        result = workflow()
    assert result[0] is values and result[1] is record and result[2] is unique
    assert len(captured) == 1
    tool_call = nh.oversight.ToolCall(nh.ExecutionRef("run", "scope", "step"), "lookup", {"values": values}, "program")
    assert tool_call.argument_name_to_value["values"] is values


def test_raise_to_pass_does_not_resurrect_unvalidated_writes() -> None:
    class Executor:
        def run_step(self, **arguments: Any) -> tuple[RaiseStepOutcome, dict[str, object]]:
            return RaiseStepOutcome(kind="raise", raise_message="failed"), {"result": "invalid"}

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        assert commit.binding_name_to_value == {}
        assert isinstance(commit.outcome, nh.oversight.Raise)
        return nh.oversight.Rewrite(outcome=nh.oversight.Pass())

    @nh.natural_function
    def workflow() -> int:
        result: int = 3
        """natural
        <:result>
        Raise an error.
        """
        return result

    with nh.run(Executor()), nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=inspect)):
        assert workflow() == 3


@pytest.mark.parametrize("error_type", ["MissingException", "invalid_exception"])
@pytest.mark.parametrize("decision", [nh.oversight.Accept(), nh.oversight.Rewrite(outcome=nh.oversight.Pass())])
def test_invalid_raise_type_fails_before_inspection(error_type: str, decision: nh.oversight.StepCommitDecision) -> None:
    inspected: list[nh.oversight.StepCommit] = []
    invalid_exception = int  # noqa: F841

    class Executor:
        def run_step(self, **arguments: Any) -> tuple[RaiseStepOutcome, dict[str, object]]:
            return RaiseStepOutcome(kind="raise", raise_message="failed", raise_error_type=error_type), {}

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.StepCommitDecision:
        inspected.append(commit)
        return decision

    @nh.natural_function
    def workflow() -> int:
        """natural
        <invalid_exception>
        Raise an error.
        """
        return 7

    with (
        nh.run(Executor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=inspect)),
        pytest.raises(nh.ExecutionError, match="Invalid raise_error_type"),
    ):
        workflow()
    assert inspected == []


def test_raise_rewrite_validates_replacement_without_constructing_original_exception() -> None:
    constructed: list[str] = []

    class OriginalError(Exception):
        def __init__(self, message: str) -> None:
            constructed.append(message)
            super().__init__(message)

    class Executor:
        def run_step(self, **arguments: Any) -> tuple[RaiseStepOutcome, dict[str, object]]:
            return RaiseStepOutcome(kind="raise", raise_message="original", raise_error_type="OriginalError"), {}

    @nh.natural_function
    def workflow() -> int:
        """natural
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
                    inspect_step_commit=lambda commit: nh.oversight.Rewrite(outcome=nh.oversight.Raise("replacement", "MissingException"))
                )
            ),
            pytest.raises(nh.ExecutionError, match="Invalid raise_error_type"),
        ):
            workflow()
    assert constructed == []


@pytest.mark.parametrize(
    "construct",
    [
        lambda: nh.oversight.Rewrite(),
        lambda: nh.oversight.Rewrite(outcome=None),  # type: ignore[arg-type]
        lambda: nh.oversight.Rewrite(binding_name_to_value=None),  # type: ignore[arg-type]
        lambda: nh.oversight.Rewrite(outcome=nh.oversight.Return(None), return_value=None),
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

    @nh.natural_function
    def workflow() -> tuple[int, int]:
        first: int = 1
        second: int = 2
        try:
            """natural
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
    @nh.natural_function
    def workflow() -> None:
        """natural
        {"step_outcome": {"kind": "pass"}, "bindings": {}}
        """

    for decision in (
        nh.oversight.Rewrite(binding_name_to_value={"unknown": 1}),
        nh.oversight.Rewrite(outcome=nh.oversight.Raise("failed"), binding_name_to_value={"unknown": 1}),
    ):
        with (
            nh.run(StubExecutor()),
            nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=lambda commit, decision=decision: decision)),
            pytest.raises(nh.NighthawkError),
        ):
            workflow()
