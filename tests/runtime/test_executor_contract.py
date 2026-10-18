from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated

import pytest
from pydantic import AfterValidator, TypeAdapter, ValidationError

import nighthawk as nh
from nighthawk.lifecycle import StepFinished, StepLifecycle, StepRaised
from nighthawk.oversight import Accept, Oversight, Rewrite
from nighthawk.runtime import OperationFinished, Pass, Raise, Return, StepCandidate, StepRequest


@dataclass
class Record:
    value: int


def test_candidate_preserves_live_values_and_freezes_mapping_membership() -> None:
    record = Record(4)
    values: list[object] = [record]
    proposed = {"value": values}
    candidate = StepCandidate(outcome=Return(record), binding_name_to_value=proposed)
    assert not isinstance(candidate.binding_name_to_value, nh.UnsetType)
    proposed["value"] = []
    assert candidate.binding_name_to_value["value"] is values
    assert isinstance(candidate.outcome, Return) and candidate.outcome.value is record
    with pytest.raises(TypeError):
        candidate.binding_name_to_value["value"] = []  # type: ignore[index]
    with pytest.raises(ValidationError):
        candidate.outcome = Pass()
    values.append("visible mutation")
    assert candidate.binding_name_to_value["value"] == [record, "visible mutation"]
    parsed = TypeAdapter(StepCandidate).validate_python({"outcome": {"kind": "return", "value": record}})
    assert isinstance(parsed.outcome, Return) and parsed.outcome.value is record


@pytest.mark.parametrize("asynchronous", [False, True])
def test_resolved_executor_value_is_never_evaluated_as_python(asynchronous: bool) -> None:
    operations: list[OperationFinished] = []
    requests: list[StepRequest] = []

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            requests.append(request)
            assert request.execution_reference == nh.get_execution_reference()
            assert request.step_context is nh.get_step_context()
            assert request.output_binding_names == ("value",)
            assert request.binding_name_to_type == {"value": int}
            assert request.return_annotation is str
            assert request.allow_awaitable_return is asynchronous
            assert request.allowed_step_kinds == ("pass", "return", "raise")
            with pytest.raises(TypeError):
                request.binding_name_to_type["value"] = str  # type: ignore[index]
            return StepCandidate(outcome=Return("raise_if_evaluated()"), binding_name_to_value={"value": "7"})

    @nh.natural_function
    def synchronous() -> str:
        value: int = 1
        """natural
        Set <:value> and return a string.
        """
        return str(value)

    @nh.natural_function
    async def asynchronous_function() -> str:
        value: int = 1
        """natural
        Set <:value> and return a string.
        """
        return str(value)

    commits: list[nh.oversight.StepCommit] = []

    def inspect(commit: nh.oversight.StepCommit) -> Accept:
        commits.append(commit)
        return Accept()

    with (
        nh.run(Executor()),
        nh.scope(oversight=Oversight(inspect_step_commit=inspect), lifecycle=StepLifecycle(on_operation_finished=operations.append)),
    ):
        assert (asyncio.run(asynchronous_function()) if asynchronous else synchronous()) == "raise_if_evaluated()"
    assert len(requests) == len(commits) == 1
    assert commits[0].binding_name_to_value == {"value": 7}
    assert operations == []


def test_selected_contract_survives_executor_context_replacement() -> None:
    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            request.step_context.binding_name_to_type = {"value": str}
            return StepCandidate(outcome=Pass(), binding_name_to_value={"value": "7"})

    @nh.natural_function
    def workflow() -> int:
        value: int = 1
        """natural
        Set <:value>.
        """
        return value

    with nh.run(Executor()):
        assert workflow() == 7


@pytest.mark.parametrize("outcome", [Pass(), Raise("failed")])
def test_custom_candidate_cannot_propose_undeclared_outputs(outcome: Pass | Raise) -> None:
    commits: list[nh.oversight.StepCommit] = []

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return StepCandidate(outcome=outcome, binding_name_to_value={"unexpected": object()})

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    with (
        nh.run(Executor()),
        nh.scope(oversight=Oversight(inspect_step_commit=lambda commit: commits.append(commit) or Accept())),
        pytest.raises(
            nh.ExecutionError, match="Raise cannot include output bindings" if isinstance(outcome, Raise) else "Undeclared output.*unexpected"
        ),
    ):
        workflow()
    assert commits == []


def test_initial_outputs_and_candidate_return_have_independent_validation() -> None:
    validations: list[int] = []

    def increment(value: int) -> int:
        validations.append(value)
        return value + 1

    Counted = Annotated[int, AfterValidator(increment)]  # noqa: N806

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return StepCandidate(outcome=Return(3))

    @nh.natural_function
    def workflow() -> Counted:  # type: ignore[valid-type]
        nonlocal Counted
        value: Counted = 3  # type: ignore[valid-type]
        """natural
        <:value>
        Finish.
        """
        return value

    with nh.run(Executor()), nh.scope(oversight=Oversight(inspect_step_commit=lambda commit: Accept())):
        assert workflow() == 4
    assert validations == [3, 3]


def test_resolved_raise_is_constructed_only_after_commit_inspection() -> None:
    calls: list[str] = []
    terminal: list[StepFinished] = []

    class DomainError(Exception):
        def __init__(self, message: str) -> None:
            calls.append("constructed")
            super().__init__(message)

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return StepCandidate(outcome=Raise("domain", DomainError))

    def inspect(commit: nh.oversight.StepCommit) -> Accept:
        assert isinstance(commit.outcome, Raise) and commit.outcome.error_type is DomainError
        assert calls == []
        calls.append("inspected")
        return Accept()

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    with (
        nh.run(Executor()),
        nh.scope(oversight=Oversight(inspect_step_commit=inspect), lifecycle=StepLifecycle(terminal.append)),
        pytest.raises(DomainError, match="domain") as caught,
    ):
        workflow()
    assert calls == ["inspected", "constructed"]
    assert isinstance(terminal[0], StepRaised) and terminal[0].exception is caught.value
    calls.clear()
    with nh.run(Executor()), nh.scope(oversight=Oversight(inspect_step_commit=lambda commit: Rewrite(outcome=Pass()))):
        workflow()
    assert calls == []


@pytest.mark.parametrize("invalid", [None, "ValueError", int, ValueError("instance")])
def test_raise_requires_a_resolved_exception_class(invalid: object) -> None:
    with pytest.raises(TypeError, match="resolved exception class"):
        Raise("failed", invalid)  # type: ignore[arg-type]
    assert Raise("failed").error_type is Exception


_global_output: int = 1


def _change_global_output() -> None:
    global _global_output
    _global_output = 7


@pytest.mark.parametrize("nonlocal_output", [False, True])
def test_finalization_reads_current_python_output_associations(nonlocal_output: bool) -> None:
    global _global_output
    _global_output = 1
    value: int = 1

    def change_value() -> None:
        nonlocal value
        value = 7

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            nh.runtime.eval_expression(request.step_context, "change_value()" if nonlocal_output else "_change_global_output()")
            assert request.snapshot_output_bindings() == {"value" if nonlocal_output else "_global_output": 7}
            return StepCandidate(outcome=Pass())

    @nh.natural_function
    def global_workflow() -> None:
        global _global_output
        """natural
        Update <:_global_output> through the helper.
        """

    @nh.natural_function
    def nonlocal_workflow() -> None:
        nonlocal value, change_value
        """natural
        Update <:value> through the helper.
        """

    try:
        with nh.run(Executor()):
            nonlocal_workflow() if nonlocal_output else global_workflow()
        assert (value if nonlocal_output else _global_output) == 7
    finally:
        _global_output = 1


def test_deleted_python_output_is_not_resurrected_at_finalization() -> None:
    value: int = 1
    commits: list[nh.oversight.StepCommit] = []

    def delete_value() -> None:
        nonlocal value
        del value

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            nh.runtime.eval_expression(request.step_context, "delete_value()")
            assert request.snapshot_output_bindings() == {}
            return StepCandidate(outcome=Pass())

    @nh.natural_function
    def workflow() -> int:
        nonlocal value, delete_value
        """natural
        Process <:value>.
        """
        return value

    with (
        nh.run(Executor()),
        nh.scope(oversight=Oversight(inspect_step_commit=lambda commit: commits.append(commit) or Accept())),
        pytest.raises(NameError),
    ):
        workflow()
    assert len(commits) == 1 and not commits[0].binding_name_to_value
