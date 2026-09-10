from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import nighthawk as nh
from nighthawk.lifecycle import StepLifecycle
from nighthawk.oversight import Accept, Oversight, OversightRejectedError, Reject
from nighthawk.runtime import OperationFinished, OperationRequest, StepRequest
from tests.governance.test_operations import new_context


@pytest.mark.parametrize("reject_first", [False, True])
def test_ordered_model_batch_remains_ordered_across_async_capability_hooks(reject_first: bool) -> None:
    effects: list[int] = []
    inspected: list[int] = []
    finished: list[OperationFinished] = []
    model_calls = 0

    async def record(value: int) -> int:
        effects.append(value)
        return value

    async def delay_first(context: Any, *, call: ToolCallPart, tool_def: Any, args: Any) -> Any:
        if call.tool_call_id == "first":
            await asyncio.sleep(0.02)
        return args

    async def inspect(request: OperationRequest) -> Accept | Reject:
        value = request.argument_name_to_value["value"]
        assert isinstance(value, int)
        inspected.append(value)
        await asyncio.sleep(0)
        return Reject("first call denied") if reject_first and value == 1 else Accept()

    async def model_function(messages: Any, information: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(parts=[ToolCallPart("record", {"value": 1}, "first"), ToolCallPart("record", {"value": 2}, "second")])
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])

    executor = nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=FunctionModel(model_function)))
    context = new_context()
    request = StepRequest(
        execution_reference=context.execution_reference,
        processed_natural_program="Record one then two.",
        step_context=context,
        output_binding_names=(),
        binding_name_to_type={},
        return_annotation=object,
        allowed_step_kinds=("pass",),
    )
    with (
        nh.run(executor),
        nh.scope(
            tools=[record],
            capabilities=[Hooks(before_tool_execute=delay_first)],
            oversight=Oversight(inspect_operation=inspect),
            lifecycle=StepLifecycle(on_operation_finished=finished.append),
        ),
    ):
        if reject_first:
            with pytest.raises(OversightRejectedError, match="first call denied"):
                asyncio.run(executor.run_step_async(request=request))
            assert effects == []
            assert inspected == [1]
            assert [event.status for event in finished] == ["rejected"]
            assert model_calls == 1
        else:
            assert asyncio.run(executor.run_step_async(request=request)).outcome.kind == "pass"
            assert effects == inspected == [1, 2]
            assert [event.status for event in finished] == ["succeeded", "succeeded"]
            assert model_calls == 2
