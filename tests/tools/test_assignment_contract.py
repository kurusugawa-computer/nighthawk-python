from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Self

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

import nighthawk as nh
from nighthawk.runtime import OperationError, assign_tool, eval_expression


def context_for(**name_to_value: object) -> nh.StepContext:
    return nh.StepContext(
        execution_reference=nh.ExecutionReference("run", "scope", "step", "test"),
        step_globals={},
        step_locals=name_to_value,
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        task_name_to_value={},
    )


def test_assignment_preserves_model_constraints_and_target_identity() -> None:
    class Finding(BaseModel, validate_assignment=True):
        confidence: float = Field(ge=0, le=1)

    finding = Finding(confidence=0.5)
    context = context_for(finding=finding)
    result = assign_tool(context, "finding.confidence", "'0.75'")
    assert result.value == finding.confidence == 0.75
    assert context.environment.resolve("finding") is finding
    with pytest.raises(OperationError) as caught:
        assign_tool(context, "finding.confidence", "2.0")
    assert isinstance(caught.value.__cause__, ValidationError)
    assert caught.value.phase == "validation"
    assert caught.value.evaluation_started
    assert finding.confidence == 0.75


def test_standard_assignment_runs_field_and_model_validators_once() -> None:
    calls: list[str] = []

    class Record(BaseModel, validate_assignment=True):
        value: int = 0

        @field_validator("value")
        @classmethod
        def increment(cls, value: int) -> int:
            calls.append("field")
            return value + 1

        @model_validator(mode="after")
        def record_call(self) -> Self:
            calls.append("model")
            return self

    record = Record()
    calls.clear()
    result = assign_tool(context_for(record=record), "record.value", "'7'")
    assert calls == ["field", "model"]
    assert result.value == record.value == 8


def test_failed_model_validator_can_leave_earlier_mutation() -> None:
    class Record(BaseModel, validate_assignment=True):
        value: int = 0

        @model_validator(mode="after")
        def reject(self) -> Self:
            if self.value == 7:
                raise ValueError("rejected after field assignment")
            return self

    record = Record()
    with pytest.raises(OperationError) as caught:
        assign_tool(context_for(record=record), "record.value", "7")
    assert record.value == 7
    assert "effects may remain" in (caught.value.guidance or "")


@pytest.mark.parametrize("frozen_model", [False, True])
def test_frozen_restrictions_are_preserved(frozen_model: bool) -> None:
    class Record(BaseModel, validate_assignment=True, frozen=frozen_model):
        value: int = Field(default=0, frozen=not frozen_model)

    record = Record()
    with pytest.raises(OperationError):
        assign_tool(context_for(record=record), "record.value", "7")
    assert record.value == 0


@pytest.mark.parametrize("route", ["dataclass", "plain", "disabled", "override", "property", "undeclared"])
def test_unsupported_attribute_routes_require_python_helpers(route: str) -> None:
    @dataclass
    class Data:
        value: int = 0

    class Plain:
        value: int = 0

    class Disabled(BaseModel):
        value: int = 0

    class Override(BaseModel, validate_assignment=True):
        value: int = 0

        def __setattr__(self, name: str, value: object) -> None:
            raise AssertionError("Custom setters must not execute")

    class WithProperty(BaseModel, validate_assignment=True):
        stored: int = 0

        @property
        def value(self) -> int:
            raise AssertionError("Final getters must not execute")

        @value.setter
        def value(self, value: int) -> None:
            raise AssertionError("Property setters must not execute")

    class Ordinary(BaseModel, validate_assignment=True):
        other: int = 0

    route_to_instance = {
        "dataclass": Data(),
        "plain": Plain(),
        "disabled": Disabled(),
        "override": Override(),
        "property": WithProperty(),
        "undeclared": Ordinary(),
    }
    effects: list[str] = []
    context = context_for(target=route_to_instance[route], effects=effects)
    with pytest.raises(OperationError) as caught:
        assign_tool(context, "target.value", "(effects.append('right'), 7)[1]")
    assert effects == ["right"]
    assert caught.value.phase == "target_resolution"
    assert "Python helper" in (caught.value.guidance or "")


def test_right_side_changes_target_before_single_intermediate_traversal() -> None:
    class Record(BaseModel, validate_assignment=True):
        value: int = 0

    accesses: list[str] = []
    first, second = Record(), Record()

    class Container:
        selected = first

        @property
        def child(self) -> Record:
            accesses.append("get")
            return self.selected

    container = Container()

    def change_target() -> int:
        accesses.append("right")
        container.selected = second
        return 7

    assign_tool(context_for(container=container, change_target=change_target), "container.child.value", "change_target()")
    assert accesses == ["right", "get"]
    assert first.value == 0 and second.value == 7


def test_admission_rejection_prevents_right_side_effects() -> None:
    effects: list[str] = []
    context = context_for(value=0, effects=effects)
    for target in ("value", "missing.field", "value[0]", "__private", "len"):
        with pytest.raises(OperationError) as caught:
            assign_tool(context, target, "effects.append('right')")
        assert not caught.value.evaluation_started
    assert effects == []


def test_assignment_validation_failure_preserves_pending_value_and_right_side_effects() -> None:
    effects: list[str] = []
    context = nh.StepContext(
        execution_reference=nh.ExecutionReference("run", "scope", "step", "test"),
        step_globals={},
        step_locals={"value": 0, "effects": effects},
        binding_commit_targets={"value"},
        read_binding_names=frozenset(),
        task_name_to_value={},
        binding_name_to_type={"value": Annotated[int, Field(ge=0)]},
    )
    assign_tool(context, "value", "'7'")
    with pytest.raises(OperationError):
        assign_tool(context, "value", "(effects.append('right'), -1)[1]")
    assert context.environment.resolve("value") == 7
    assert effects == ["right"]


@pytest.mark.parametrize("exception_type", [NameError, ValueError, TypeError])
def test_helper_failures_propagate_without_retry_conversion(exception_type: type[Exception]) -> None:
    error = exception_type("application failure")

    def fail() -> None:
        raise error

    with pytest.raises(exception_type) as caught:
        eval_expression(context_for(fail=fail), "fail()")
    assert caught.value is error


def test_lookup_failure_is_recoverable_and_named_assignment_is_rejected() -> None:
    context = context_for()
    with pytest.raises(OperationError) as caught:
        eval_expression(context, "missing")
    assert caught.value.phase == "evaluation"
    with pytest.raises(OperationError) as caught:
        eval_expression(context, "(value := 7)")
    assert caught.value.phase == "admission"
    assert not caught.value.evaluation_started
