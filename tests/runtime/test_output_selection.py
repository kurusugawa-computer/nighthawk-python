from collections.abc import Awaitable, Callable
from typing import Annotated

import pytest
from pydantic import AfterValidator, ValidationError

import nighthawk as nh
from nighthawk.lifecycle import StepFinished, StepLifecycle
from nighthawk.oversight import Accept, Oversight, Rewrite
from nighthawk.runtime import Pass, Raise, StepCandidate, StepDefinition, StepRequest, StepResult
from nighthawk.testing import CallbackExecutor, ScriptedExecutor, StepResponse, pass_response
from tests.support.executors import InspectingExecutor


@pytest.mark.parametrize("selection", ["omitted", "unset", "empty", "explicit", "snapshot"])
@pytest.mark.asyncio
async def test_candidate_selects_exact_outputs_before_validation_and_assignment(
    execute: Callable[..., Awaitable[StepResult]], selection: str
) -> None:
    validated: list[int] = []
    assigned_binding_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []
    commits: list[object] = []

    def validate(value: int) -> int:
        validated.append(value)
        return value + 100

    def execute_candidate(request: StepRequest) -> StepCandidate:
        request.step_context.record_assignment("first", 11)
        request.step_context.record_assignment("temporary", "not an output")
        if selection == "omitted":
            return StepCandidate(outcome=Pass())
        if selection == "unset":
            return StepCandidate(outcome=Pass(), binding_name_to_value=nh.UNSET)
        if selection == "empty":
            return StepCandidate(outcome=Pass(), binding_name_to_value={})
        if selection == "explicit":
            return StepCandidate(outcome=Pass(), binding_name_to_value={"first": "31"})
        snapshot_binding_name_to_value = request.snapshot_output_bindings()
        assert snapshot_binding_name_to_value == {"first": 11, "second": 20}
        assert list(snapshot_binding_name_to_value) == ["first", "second"]
        assert validated == [] and assigned_binding_name_to_value == {}
        request.step_context.record_assignment("second", 99)
        return StepCandidate(outcome=Pass(), binding_name_to_value={**snapshot_binding_name_to_value, "first": "31"})

    def inspect(commit):
        commits.append(dict(commit.binding_name_to_value))
        return Accept()

    contract = Annotated[int, AfterValidator(validate)]
    with (
        nh.run(InspectingExecutor(execute_candidate)),
        nh.scope(
            task_references={"task_only": 30},
            oversight=Oversight(inspect_step_commit=inspect),
            lifecycle=StepLifecycle(events.append),
        ),
    ):
        await execute(
            StepDefinition(
                "select outputs",
                output_binding_names=("first", "second", "task_only", "unbound"),
                binding_name_to_type={"first": contract, "second": contract},
            ),
            name_to_value={"first": 10, "second": 20},
            write_binding=assigned_binding_name_to_value.__setitem__,
        )
    expected_binding_name_to_value = {
        "omitted": {"first": 111, "second": 120},
        "unset": {"first": 111, "second": 120},
        "empty": {},
        "explicit": {"first": 131},
        "snapshot": {"first": 131, "second": 120},
    }[selection]
    assert assigned_binding_name_to_value == expected_binding_name_to_value
    assert commits == [expected_binding_name_to_value]
    assert events[0].assigned_binding_name_to_value == expected_binding_name_to_value
    assert validated == [value - 100 for value in expected_binding_name_to_value.values()]


@pytest.mark.asyncio
async def test_explicit_selection_excludes_invalid_initial_output_and_keeps_live_identity(
    execute: Callable[..., Awaitable[StepResult]],
) -> None:
    shared = []
    assigned_binding_name_to_value: dict[str, object] = {}

    def candidate(request: StepRequest) -> StepCandidate:
        snapshot_binding_name_to_value = request.snapshot_output_bindings()
        assert snapshot_binding_name_to_value["shared"] is shared
        assert snapshot_binding_name_to_value["none"] is None
        return StepCandidate(outcome=Pass(), binding_name_to_value={"shared": shared, "none": None})

    with nh.run(InspectingExecutor(candidate)):
        await execute(
            StepDefinition("select", output_binding_names=("invalid", "shared", "none"), binding_name_to_type={"invalid": int, "shared": object}),
            name_to_value={"invalid": "not an integer", "shared": shared, "none": None},
            write_binding=assigned_binding_name_to_value.__setitem__,
        )
    assert assigned_binding_name_to_value == {"shared": [], "none": None}
    assert assigned_binding_name_to_value["shared"] is shared


@pytest.mark.parametrize("factory", [StepCandidate, Rewrite, StepResponse])
def test_raise_rejects_nonempty_selection_at_construction(factory) -> None:
    with pytest.raises((ValueError, ValidationError), match="Raise cannot include output bindings"):
        factory(outcome=Raise("failed"), binding_name_to_value={"value": 1})
    factory(outcome=Raise("failed"))
    factory(outcome=Raise("failed"), binding_name_to_value={})


@pytest.mark.parametrize("factory", [StepCandidate, Rewrite, StepResponse])
@pytest.mark.parametrize("invalid", [None, {1: "invalid key"}, [], nh.Merge({})])
def test_output_selection_rejects_invalid_shapes(factory, invalid) -> None:
    with pytest.raises((TypeError, ValidationError)):
        factory(outcome=Pass(), binding_name_to_value=invalid)


@pytest.mark.parametrize("callback", [False, True])
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.asyncio
async def test_testing_executors_preserve_omitted_and_empty_selection(
    execute: Callable[..., Awaitable[StepResult]], callback: bool, empty: bool
) -> None:
    response = pass_response(binding_name_to_value={}) if empty else pass_response()
    executor = CallbackExecutor(lambda call: response) if callback else ScriptedExecutor([response])
    assigned_binding_name_to_value: dict[str, object] = {}
    with nh.run(executor):
        await execute(
            StepDefinition("select", output_binding_names=("value",)),
            name_to_value={"value": 7},
            write_binding=assigned_binding_name_to_value.__setitem__,
        )
    assert assigned_binding_name_to_value == ({} if empty else {"value": 7})
