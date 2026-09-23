from collections.abc import Awaitable, Callable
from dataclasses import FrozenInstanceError

import pytest

import nighthawk as nh
from nighthawk.lifecycle import StepFinished, StepLifecycle
from nighthawk.runtime import BindingDeclarationError, Pass, Return, StepCandidate, StepDefinition, StepRequest, StepResult
from tests.support.executors import InspectingExecutor


@pytest.mark.parametrize("field", ["input_binding_names", "output_binding_names"])
def test_definition_validates_both_declaration_lists(field: str) -> None:
    with pytest.raises(BindingDeclarationError):
        if field == "input_binding_names":
            StepDefinition("write", input_binding_names=("result", "result"))
        else:
            StepDefinition("write", output_binding_names=("result", "result"))


def test_definition_rejects_annotations_without_output_declarations() -> None:
    with pytest.raises(BindingDeclarationError) as caught:
        StepDefinition("write", binding_name_to_type={"result": int})
    assert caught.value.binding_names == ("result",)


def test_definition_freezes_declarations_and_preserves_annotation_objects() -> None:
    input_names = ["source"]
    output_names = ["result"]
    annotation = list[int]
    declared_name_to_type: dict[str, object] = {"result": annotation}
    definition = StepDefinition(
        "write", input_binding_names=input_names, output_binding_names=output_names, binding_name_to_type=declared_name_to_type
    )
    input_names.clear()
    output_names.clear()
    declared_name_to_type.clear()
    assert definition.input_binding_names == ("source",)
    assert definition.output_binding_names == ("result",)
    assert definition.binding_name_to_type["result"] is annotation
    with pytest.raises(TypeError):
        definition.binding_name_to_type["result"] = str  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        definition.natural_program = "changed"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_reused_definition_resolves_each_invocations_environment(execute: Callable[..., Awaitable[StepResult]]) -> None:
    definition = StepDefinition(
        "write", output_binding_names=("result", "inferred"), binding_name_to_type={"result": "OutputType"}, return_annotation="OutputType"
    )
    observed: list[dict[str, object]] = []
    events: list[StepFinished] = []

    def inspect(request: StepRequest) -> StepCandidate:
        observed.append(dict(request.binding_name_to_type))
        return StepCandidate(outcome=Return("7"), binding_name_to_value={"result": "7", "inferred": "8"})

    with nh.run(InspectingExecutor(inspect)), nh.scope(lifecycle=StepLifecycle(events.append)):
        for selected_type, initial, expected_result, expected_inferred in [(int, 0, 7, 8), (str, "", "7", "8")]:
            saved_name_to_value: dict[str, object] = {}
            outcome = await execute(
                definition, name_to_value={"OutputType": selected_type, "inferred": initial}, write_binding=saved_name_to_value.__setitem__
            )
            assert outcome == Return(expected_result)
            assert saved_name_to_value == {"result": expected_result, "inferred": expected_inferred}
    assert observed == [{"result": int, "inferred": int}, {"result": str, "inferred": str}]
    assert events[0].execution_reference.step_execution_id != events[1].execution_reference.step_execution_id
    assert definition.binding_name_to_type == {"result": "OutputType"}


@pytest.mark.asyncio
async def test_failed_invocation_does_not_poison_reusable_definition(execute: Callable[..., Awaitable[StepResult]]) -> None:
    definition = StepDefinition("write", output_binding_names=("result",), binding_name_to_type={"result": "OutputType"})
    observed: list[StepRequest] = []

    def inspect(request: StepRequest) -> StepCandidate:
        observed.append(request)
        return StepCandidate(outcome=Pass(), binding_name_to_value={"result": "7"})

    saved_name_to_value: dict[str, object] = {}
    with nh.run(InspectingExecutor(inspect)):
        with pytest.raises(nh.ExecutionError, match="OutputType"):
            await execute(definition, name_to_value={}, write_binding=saved_name_to_value.__setitem__)
        await execute(definition, name_to_value={"OutputType": int}, write_binding=saved_name_to_value.__setitem__)
    assert len(observed) == 1
    assert saved_name_to_value == {"result": 7}
