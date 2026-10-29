from __future__ import annotations

import inspect

import pytest

import nighthawk as nh
from nighthawk.runtime import Pass, StepDefinition, describe_value, execute_step_async
from tests.support.model_evaluation import ModelEvaluation

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("helper", ["builtins", "standard_library", "describe_value"])
async def test_omitted_member_details_with_explicit_helpers(model_evaluation: ModelEvaluation, helper: str) -> None:
    property_reads: list[str] = []

    class Inventory:
        def __init__(self) -> None:
            self.a_count = 0
            self.b_count = -1
            self.c_count = 5
            self.d_count = 0
            self.e_count = 8
            self.f_count = -3
            self.g_count = 0
            self.h_count = 13
            self.i_count = 2
            self.j_count = 3
            self.k_count = 4
            self.label = "warehouse"

        @property
        def remote_count(self) -> int:
            property_reads.append("remote_count")
            raise RuntimeError("Reading this property would perform an external operation")

    value = Inventory()
    name_to_value: dict[str, object] = {"value": value}
    if helper == "standard_library":
        name_to_value["inspect"] = inspect
    elif helper == "describe_value":
        name_to_value["describe_value"] = describe_value
    committed_name_to_value: dict[str, object] = {}
    model_evaluation.name_to_observation.update(bindings=committed_name_to_value, property_reads=property_reads)
    configuration = model_evaluation.configuration.model_copy(
        update={"context_limits": nh.StepContextLimits(object_max_fields=0, object_max_methods=0)}
    )
    with nh.scope(step_executor_configuration=configuration):
        outcome = await execute_step_async(
            StepDefinition(
                "Set <:result> to a sorted list of names of the public instance fields on <value> "
                "that end in '_count' and hold a positive integer. Use the live value, include all matching fields, "
                "and do not evaluate properties.",
                input_binding_names=tuple(name_to_value),
                output_binding_names=("result",),
                binding_name_to_type={"result": list[str]},
            ),
            name_to_value=name_to_value,
            write_binding=committed_name_to_value.__setitem__,
        )
    assert outcome == Pass()
    assert committed_name_to_value == {"result": ["c_count", "e_count", "h_count", "i_count", "j_count", "k_count"]}
    assert property_reads == []
