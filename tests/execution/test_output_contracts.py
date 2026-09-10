from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import AfterValidator, Field

import nighthawk as nh
from nighthawk.lifecycle import StepFailed, StepFinished, StepLifecycle
from nighthawk.runtime import StepCandidate, StepRequest
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.model_candidate import resolve_model_candidate
from nighthawk.runtime.step_contract import PassStepOutcome, RaiseStepOutcome
from tests.execution.stub_executor import StubExecutor

_global_contract_result: Annotated[int, Field(ge=0, le=10)] = 0


def test_global_output_uses_module_annotation_and_writes_back() -> None:
    @nh.natural_function
    def workflow() -> int:
        global _global_contract_result
        """natural
        <:_global_contract_result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"_global_contract_result": "7"}}
        """
        return _global_contract_result

    global _global_contract_result
    original_value = _global_contract_result
    try:
        with nh.run(StubExecutor()):
            assert workflow() == 7
        assert _global_contract_result == 7
    finally:
        _global_contract_result = original_value


def test_nonlocal_output_infers_live_value_without_outer_annotation() -> None:
    result: object = "initial"

    @nh.natural_function
    def workflow() -> object:
        nonlocal result
        """natural
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": "7"}}
        """
        return result

    result = 1
    with nh.run(StubExecutor()):
        assert workflow() == 7
    assert result == 7


def test_return_annotation_resolution_fails_before_executor() -> None:
    @nh.natural_function
    def workflow() -> UnavailableReturnContract:  # type: ignore[name-defined]  # noqa: F821
        """natural
        This executor would fail if reached.
        """

    events: list[StepFinished] = []
    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=events.append)),
        pytest.raises(nh.ExecutionError, match="UnavailableReturnContract"),
    ):
        workflow()
    assert len(events) == 1
    assert isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"


def test_explicit_object_and_any_do_not_infer_initial_type() -> None:
    @nh.natural_function
    def workflow() -> tuple[object, Any]:
        first: object = "initial"
        second: Any = "initial"
        """natural
        <:first> <:second>
        {"step_outcome": {"kind": "pass"}, "bindings": {"first": 7, "second": [1]}}
        """
        return first, second

    with nh.run(StubExecutor()):
        assert workflow() == (7, [1])


def test_compound_annotation_is_static_and_preserves_metadata() -> None:
    @nh.natural_function
    def workflow() -> int:
        if False:
            result: Annotated[int, Field(ge=0, le=10)]
        """natural
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": "7"}}
        """
        return result  # type: ignore[name-defined]  # noqa: F821

    with nh.run(StubExecutor()):
        assert workflow() == 7


def test_compound_annotation_rejects_out_of_range_value() -> None:
    @nh.natural_function
    def workflow() -> int:
        if True:
            result: Annotated[int, Field(ge=0, le=10)] = 0
        """natural
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": 11}}
        """
        return result

    with nh.run(StubExecutor()), pytest.raises(nh.ExecutionError, match="less than or equal to 10"):
        workflow()


def test_ambiguous_annotations_fail_preparation_before_executor() -> None:
    @nh.natural_function
    def workflow(condition: bool) -> object:
        if condition:
            result: int = 0  # pyright: ignore[reportRedeclaration, reportAssignmentType]
        else:
            result: str = ""
        """natural
        <:result>
        """
        return result

    events: list[StepFinished] = []
    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=events.append)),
        pytest.raises(nh.ExecutionError, match="Ambiguous annotation"),
    ):
        workflow(True)
    assert len(events) == 1
    assert isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"


def test_unresolved_annotation_fails_preparation_with_identity() -> None:
    @nh.natural_function
    def workflow() -> object:
        result: UnavailableContract = "initial"  # type: ignore[name-defined]  # noqa: F821
        """natural
        <:result>
        """
        return result

    events: list[StepFinished] = []
    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=events.append)),
        pytest.raises(nh.ExecutionError, match="UnavailableContract"),
    ):
        workflow()
    assert len(events) == 1
    assert isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"
    assert events[0].execution_reference.step_execution_id is not None


def test_nested_scope_annotations_do_not_override_output_contract() -> None:
    @nh.natural_function
    def workflow() -> int:
        result: int = 0

        def helper() -> str:
            result: str = "nested"
            return result

        class Nested:
            result: str = "nested"

        """natural
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": "7"}}
        """
        return result

    with nh.run(StubExecutor()):
        assert workflow() == 7


def test_initialized_output_is_validated_without_executor_assignment() -> None:
    @nh.natural_function
    def workflow() -> str:
        result: Annotated[str, AfterValidator(str.strip)] = " draft "
        """natural
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {}}
        """
        return result

    with nh.run(StubExecutor()):
        assert workflow() == "draft"


def test_invalid_initialized_output_fails_without_executor_assignment() -> None:
    @nh.natural_function
    def workflow() -> int:
        result: Annotated[int, Field(ge=0)] = -1
        """natural
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {}}
        """
        return result

    with nh.run(StubExecutor()), pytest.raises(nh.ExecutionError, match="greater than or equal to 0"):
        workflow()


def test_explicit_none_output_is_a_contract() -> None:
    @nh.natural_function
    def workflow() -> None:
        result: None = None
        """natural
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": 1}}
        """
        return result

    with nh.run(StubExecutor()), pytest.raises(nh.ExecutionError, match="None"):
        workflow()


@pytest.mark.parametrize("raising", [False, True])
def test_undeclared_proposals_are_rejected_even_for_raise(raising: bool) -> None:
    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            outcome = RaiseStepOutcome(kind="raise", raise_message="failed") if raising else PassStepOutcome(kind="pass")
            return run_coroutine_synchronously(lambda: resolve_model_candidate(request, outcome, {"undeclared": 1}))

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    with nh.run(Executor()), pytest.raises(nh.ExecutionError, match="Undeclared output"):
        workflow()
