import pytest

import nighthawk as nh
from nighthawk.runtime import Pass, StepDefinition, execute_step_async
from tests.support.model_evaluation import ModelEvaluation


@pytest.mark.asyncio
async def test_explicit_task_source_survives_python_shadowing(model_evaluation: ModelEvaluation) -> None:
    name_to_value: dict[str, object] = {}
    model_evaluation.name_to_observation.update(bindings=name_to_value)
    with nh.scope(task_references={"value": 100}):
        outcome = await execute_step_async(
            StepDefinition(
                "Set <:result> to the task-scoped binding named 'value', even though a Python binding with that name is visible.",
                input_binding_names=("value",),
                output_binding_names=("result",),
            ),
            name_to_value={"value": 7},
            write_binding=name_to_value.__setitem__,
        )
    assert outcome == Pass()
    assert name_to_value == {"result": 100}
