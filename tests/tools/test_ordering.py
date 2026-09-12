from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai import Agent, Tool
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import nighthawk as nh
from nighthawk.lifecycle import StepLifecycle
from nighthawk.oversight import Accept, Oversight, OversightRejectedError, Reject
from nighthawk.runtime import OperationFinished, OperationRequest, StepRequest
from tests.support.contexts import new_context


@pytest.mark.parametrize("reject_first", [False, True])
@pytest.mark.parametrize("end_strategy", ["early", "graceful", "exhaustive"])
def test_ordered_model_batch_remains_ordered_across_async_capability_hooks(reject_first: bool, end_strategy: Any) -> None:
    effects: list[int] = []
    validation_and_body: list[str] = []
    inspected: list[int] = []
    finished: list[OperationFinished] = []
    model_calls = 0

    async def record(value: int) -> int:
        validation_and_body.append(f"body:{value}")
        effects.append(value)
        return value

    async def validate(context: Any, value: int) -> None:
        validation_and_body.append(f"validate:{value}")
        await asyncio.sleep(0)

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

    executor = nh.AgentStepExecutor.from_agent(agent=Agent(FunctionModel(model_function), deps_type=nh.StepContext, end_strategy=end_strategy))
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
            tools=[Tool(record, args_validator=validate)],
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
            assert validation_and_body == ["validate:1"]
        else:
            assert asyncio.run(executor.run_step_async(request=request)).outcome.kind == "pass"
            assert effects == inspected == [1, 2]
            assert [event.status for event in finished] == ["succeeded", "succeeded"]
            assert model_calls == 2
            assert validation_and_body == ["validate:1", "body:1", "validate:2", "body:2"]


@pytest.mark.parametrize("end_strategy", ["early", "graceful", "exhaustive"])
def test_native_validator_failure_preserves_earlier_body_and_stops_later_validation(end_strategy: Any) -> None:
    events: list[str] = []
    original = TypeError("second validator failed")
    operations: list[OperationFinished] = []

    async def validate(context: Any, value: int) -> None:
        events.append(f"validate:{value}")
        if value == 2:
            raise original

    def record(value: int) -> int:
        events.append(f"body:{value}")
        return value

    async def respond(messages: Any, information: Any) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("record", {"value": value}, str(value)) for value in (1, 2, 3)])

    executor = nh.AgentStepExecutor.from_agent(agent=Agent(FunctionModel(respond), deps_type=nh.StepContext, end_strategy=end_strategy))

    @nh.natural_function
    async def workflow() -> None:
        """natural
        Record the three values in order.
        """

    with (
        nh.run(executor),
        nh.scope(tools=[Tool(record, args_validator=validate)], lifecycle=StepLifecycle(on_operation_finished=operations.append)),
        pytest.raises(TypeError) as caught,
    ):
        asyncio.run(workflow())
    assert caught.value is original
    assert events == ["validate:1", "body:1", "validate:2"]
    assert [(operation.status, operation.phase) for operation in operations] == [("succeeded", "completed"), ("failed", "admission")]


def test_exhaustive_output_settlement_keeps_later_valid_output_after_retry_exhaustion() -> None:
    effects: list[int] = []

    def record(value: int) -> int:
        effects.append(value)
        return value

    async def respond(messages: Any, information: Any) -> ModelResponse:
        output_name = information.output_tools[0].name
        return ModelResponse(
            parts=[
                ToolCallPart(output_name, "{", "invalid-first"),
                ToolCallPart("record", {"value": 1}, "first"),
                ToolCallPart(output_name, "{", "invalid-second"),
                ToolCallPart("record", {"value": 2}, "second"),
                ToolCallPart(output_name, {"result": {"kind": "pass"}}, "valid"),
            ]
        )

    executor = nh.AgentStepExecutor.from_agent(agent=Agent(FunctionModel(respond), deps_type=nh.StepContext, end_strategy="exhaustive"))

    @nh.natural_function
    async def workflow() -> None:
        """natural
        Complete the calls and finish.
        """

    with nh.run(executor), nh.scope(tools=[record]):
        asyncio.run(workflow())
    assert effects == [1, 2]


@pytest.mark.parametrize("end_strategy", ["early", "graceful", "exhaustive"])
def test_ordinary_pydantic_agent_keeps_its_own_validation_behavior(end_strategy: Any) -> None:
    events: list[str] = []
    requests = 0

    async def validate(context: Any, value: int) -> None:
        events.append(f"validate:{value}")

    def record(value: int) -> int:
        events.append(f"body:{value}")
        return value

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("record", {"value": value}, str(value)) for value in (1, 2)])
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(respond), tools=[Tool(record, args_validator=validate)], end_strategy=end_strategy)
    assert asyncio.run(agent.run("Record two values.")).output == "done"
    assert events[:2] == ["validate:1", "validate:2"]
    assert sorted(events[2:]) == ["body:1", "body:2"]
