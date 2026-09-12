from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Self

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

import nighthawk as nh
from nighthawk.runtime import OperationError, assign_tool
from tests.support.contexts import new_context as context_for


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


def test_validation_failure_does_not_close_nested_application_awaitable() -> None:
    import asyncio
    import inspect

    import pytest

    from nighthawk.runtime import OperationError, assign_tool_async
    from tests.support.contexts import new_context

    async def inner() -> int:
        return 5

    retained = inner()

    async def outer():
        return retained

    initial = new_context(outer=outer)
    context = type(initial)(
        execution_reference=initial.execution_reference,
        step_globals={},
        step_locals={"outer": outer},
        binding_commit_targets={"result"},
        binding_name_to_type={"result": int},
        read_binding_names=frozenset(),
        task_name_to_value={},
    )
    try:
        with pytest.raises(OperationError):
            asyncio.run(assign_tool_async(context, "result", "outer()"))
        assert inspect.getcoroutinestate(retained) == inspect.CORO_CREATED
    finally:
        retained.close()
