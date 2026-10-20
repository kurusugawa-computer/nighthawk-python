from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

import pytest
from pydantic import AfterValidator

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepRaised
from nighthawk.oversight import Accept, Oversight
from nighthawk.runtime import Pass, Raise, StepCandidate, StepDefinition, StepRequest, execute_step_async
from nighthawk.runtime.step_result import StepResult
from nighthawk.testing import ScriptedExecutor, pass_response
from tests.support.executors import InspectingExecutor


@pytest.mark.asyncio
async def test_partial_write_failure_records_only_completed_writes(execute: Callable[..., Awaitable[StepResult]]) -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    def write(name: str, value: object) -> None:
        if name == "second":
            raise ValueError("storage failed")
        saved_name_to_value[name] = value

    with (
        nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=Pass(), binding_name_to_value={"second": 2, "first": 1}))),
        nh.scope(lifecycle=StepLifecycle(events.append)),
        pytest.raises(nh.ExecutionError, match="storage failed"),
    ):
        await execute(StepDefinition("write", output_binding_names=("first", "second")), name_to_value={}, write_binding=write)
    assert saved_name_to_value == {"first": 1}
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "binding_assignment"
    assert events[0].assigned_binding_name_to_value == saved_name_to_value
    assert events[0].validated_binding_name_to_value == {"first": 1, "second": 2}


@pytest.mark.asyncio
async def test_final_raise_discards_invalid_output_and_delivers_before_propagating(execute: Callable[..., Awaitable[StepResult]]) -> None:
    events: list[StepFinished] = []
    saved_name_to_value: dict[str, object] = {}

    def candidate(request):
        request.step_context.record_assignment("result", "invalid")
        return StepCandidate(outcome=Raise("domain error", error_type=LookupError))

    with nh.run(InspectingExecutor(candidate)), nh.scope(lifecycle=StepLifecycle(events.append)), pytest.raises(LookupError):
        await execute(
            StepDefinition("raise", output_binding_names=("result",)), name_to_value={"result": 0}, write_binding=saved_name_to_value.__setitem__
        )
    assert saved_name_to_value == {}
    assert len(events) == 1 and isinstance(events[0], StepRaised)


@pytest.mark.asyncio
async def test_terminal_failure_preserves_successful_write_without_redelivery(execute: Callable[..., Awaitable[StepResult]]) -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    def finished(event: StepFinished) -> None:
        assert saved_name_to_value == {"result": 7}
        events.append(event)
        raise RuntimeError("notification failed")

    with (
        nh.run(ScriptedExecutor([pass_response(binding_name_to_value={"result": 7})])),
        nh.scope(lifecycle=StepLifecycle(finished)),
        pytest.raises(RuntimeError, match="notification failed"),
    ):
        await execute(StepDefinition("write", output_binding_names=("result",)), name_to_value={}, write_binding=saved_name_to_value.__setitem__)
    assert len(events) == 1 and isinstance(events[0], StepCompleted)
    assert events[0].assigned_binding_name_to_value == saved_name_to_value


@pytest.mark.asyncio
async def test_cancellation_during_write_joins_cleanup_and_records_completed_writes() -> None:
    entered = asyncio.Event()
    cleaned_up = asyncio.Event()
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    async def write(name: str, value: object) -> None:
        if name == "second":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned_up.set()
        saved_name_to_value[name] = value

    def finished(event: StepFinished) -> None:
        assert cleaned_up.is_set()
        events.append(event)

    with nh.run(ScriptedExecutor([pass_response(binding_name_to_value={"first": 1, "second": 2})])), nh.scope(lifecycle=StepLifecycle(finished)):
        task = asyncio.create_task(
            execute_step_async(StepDefinition("write", output_binding_names=("first", "second")), name_to_value={}, write_binding=write)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert saved_name_to_value == {"first": 1}
    assert len(events) == 1 and isinstance(events[0], StepInterrupted)
    assert events[0].failure_stage == "binding_assignment"
    assert events[0].assigned_binding_name_to_value == saved_name_to_value


@pytest.mark.asyncio
async def test_normal_and_embedded_execution_have_same_validation_and_lifecycle_order(execute: Callable[..., Awaitable[StepResult]]) -> None:
    order: list[str] = []
    observed_events: list[StepFinished] = []

    def validate(value: str) -> str:
        order.append("validate")
        return value.strip()

    OutputType = Annotated[str, AfterValidator(validate)]  # noqa: N806

    @nh.function
    def normal() -> str:
        nonlocal OutputType
        result: OutputType = " initial "  # type: ignore[valid-type]
        """execute
        Write <:result>.
        """
        return result

    def inspect_request(request: StepRequest) -> StepCandidate:
        order.append("execute")
        assert request.binding_name_to_type["result"] == OutputType
        return StepCandidate(outcome=Pass(), binding_name_to_value={"result": " result "})

    def inspect_commit(commit: nh.oversight.StepCommit) -> Accept:
        order.append("inspect")
        assert commit.binding_name_to_value == {"result": "result"}
        return Accept()

    def finished(event: StepFinished) -> None:
        order.append("finished")
        observed_events.append(event)

    with (
        nh.run(InspectingExecutor(inspect_request)),
        nh.scope(
            oversight=Oversight(inspect_step_commit=inspect_commit),
            lifecycle=StepLifecycle(finished, on_step_started=lambda event: order.append("started")),
        ),
    ):
        assert normal() == "result"
        normal_order = list(order)
        order.clear()
        saved_name_to_value: dict[str, object] = {}
        await execute(
            StepDefinition("Write <:result>.", output_binding_names=("result",), binding_name_to_type={"result": OutputType}),
            name_to_value={"result": " initial "},
            write_binding=saved_name_to_value.__setitem__,
        )
    assert order == normal_order == ["started", "execute", "validate", "inspect", "finished"]
    assert observed_events[0].assigned_binding_name_to_value == observed_events[1].assigned_binding_name_to_value == {"result": "result"}
