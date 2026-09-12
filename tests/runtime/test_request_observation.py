from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.usage import RequestUsage

import nighthawk as nh
from examples.embedded_execution import PreservingExecutor, RecordingHandler, RequestObserver, RequestRefusedError, block, child, deterministic_model


def test_nested_model_changes_account_once_before_next_admission() -> None:
    first, second = deterministic_model("A"), deterministic_model("B")
    observer, handler = RequestObserver(), RecordingHandler()
    executor = PreservingExecutor(
        nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=first)), {"allowed:B": second}, observer
    )

    @nh.natural_function
    async def parent() -> None:
        block()
        await child()
        block()

    with nh.function_call_scope(handler), nh.run(executor), nh.scope(capabilities=[observer]):
        with pytest.raises(nh.ExecutionError) as caught:
            asyncio.run(parent())
        assert isinstance(caught.value.__cause__, RequestRefusedError)
        assert observer.observed_tokens == 120
        assert nh.get_usage_meter().total_tokens == 120
    assert [record.configured_model for record in observer.records] == ["A", "B", "A"]
    assert [record.status for record in observer.records] == ["succeeded", "succeeded", "refused"]
    assert len({record.request_id for record in observer.records}) == 3
    assert len({record.step_execution_id for record in observer.records}) == 3
    assert all(call.parent_call_id == handler.calls[0].call_id for call in handler.calls[1:])
    assert [record.call_id for record in observer.records] == [call.call_id for call in handler.calls[1:]]


class ControlledModel(Model):
    def __init__(self, mode: str, entered: asyncio.Event | None = None, release: asyncio.Event | None = None) -> None:
        super().__init__()
        self.mode = mode
        self.entered, self.release = entered, release
        self.requests = 0

    @property
    def model_name(self) -> str:
        return "configured"

    @property
    def system(self) -> str:
        return "deterministic"

    async def request(self, messages: Any, model_settings: Any, model_request_parameters: Any) -> ModelResponse:
        self.requests += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.mode == "failure":
            raise RuntimeError("transport failed without usage")
        return ModelResponse(
            parts=[ToolCallPart(model_request_parameters.output_tools[0].name, {"result": {"kind": "pass"}})],
            usage=RequestUsage() if self.mode == "unknown" else RequestUsage(input_tokens=50, output_tokens=10),
            model_name=None if self.mode == "unknown" else "reported",
        )


@nh.natural_function
async def async_block() -> None:
    """natural
    Complete without changing anything.
    """


def test_unknown_metadata_and_previous_usage_survive_failure() -> None:
    observer = RequestObserver(1000)
    model = ControlledModel("unknown")
    with nh.run(nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=model))), nh.scope(capabilities=[observer]):
        block()
        model.mode = "success"
        block()
        model.mode = "failure"
        with pytest.raises(nh.ExecutionError):
            block()
    assert observer.records[0].configured_model == "configured"
    assert observer.records[0].reported_model is None
    assert observer.records[0].tokens is None
    assert observer.records[1].reported_model == "reported"
    assert observer.records[2].status == "failed" and observer.records[2].tokens is None
    assert observer.observed_tokens == 60


def test_cancellation_is_not_success_and_does_not_shutdown_caller_loop() -> None:
    async def execute() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        model = ControlledModel("success", entered, release)
        observer = RequestObserver()
        with (
            nh.run(nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=model))),
            nh.scope(capabilities=[observer]),
        ):
            task = asyncio.create_task(async_block())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert observer.records[0].status == "interrupted"
            assert observer.records[0].tokens is None
            assert observer.observed_tokens == 0
            assert not asyncio.get_running_loop().is_closed()

    asyncio.run(execute())


def test_concurrent_admissions_can_overrun_then_refuse() -> None:
    async def execute() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        model = ControlledModel("success", entered, release)
        observer = RequestObserver()
        with (
            nh.run(nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=model))),
            nh.scope(capabilities=[observer]),
        ):
            first, second = asyncio.create_task(async_block()), asyncio.create_task(async_block())
            try:
                while model.requests < 2:
                    await asyncio.sleep(0)
                assert observer.observed_tokens == 0
            finally:
                release.set()
            await asyncio.gather(first, second)
            assert observer.observed_tokens == 120
            with pytest.raises(nh.ExecutionError):
                await async_block()
            assert model.requests == 2

    asyncio.run(execute())


def test_budget_stops_third_request_inside_one_unfinished_block() -> None:
    class MultiRequestModel(ControlledModel):
        async def request(self, messages: Any, model_settings: Any, model_request_parameters: Any) -> ModelResponse:
            self.requests += 1
            return ModelResponse(
                parts=[ToolCallPart("nh_eval", {"expression": "1"}, str(self.requests))], usage=RequestUsage(input_tokens=50, output_tokens=10)
            )

    model = MultiRequestModel("success")
    observer = RequestObserver()
    with nh.run(nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=model))), nh.scope(capabilities=[observer]):
        with pytest.raises(nh.ExecutionError) as caught:
            block()
        assert isinstance(caught.value.__cause__, RequestRefusedError)
        assert model.requests == 2
        assert observer.observed_tokens == 120
        assert len({record.step_execution_id for record in observer.records}) == 1
        assert [record.status for record in observer.records] == ["succeeded", "succeeded", "refused"]
        # The independent aggregate only records an agent run that returned.
        assert nh.get_usage_meter().total_tokens == 0
