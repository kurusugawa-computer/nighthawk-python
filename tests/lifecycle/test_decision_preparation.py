import asyncio
from collections.abc import Awaitable, Callable

import pytest

import nighthawk as nh
from nighthawk.lifecycle import StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepStarted
from nighthawk.runtime import StepDefinition, StepOutcome
from nighthawk.testing import ScriptedExecutor


@nh.function
def judge(instruction: Callable[[], str]) -> bool:
    result = False
    f"""decide <:result>
    {instruction()}
    """
    return result


@nh.function
async def judge_async(instruction: Callable[[], str]) -> bool:
    result = False
    f"""decide <:result>
    {instruction()}
    """
    return result


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["rendering", "empty_instruction", "started_delivery", "rendering_interrupted", "started_delivery_interrupted"])
@pytest.mark.asyncio
async def test_decision_identity_survives_failure_before_preparation(asynchronous: bool, failure: str) -> None:
    events: list[StepFinished] = []
    started: list[StepStarted] = []
    rendered: list[bool] = []
    executor = ScriptedExecutor([])
    original_exception = asyncio.CancelledError("interrupted") if failure.endswith("interrupted") else ValueError("unavailable")

    def instruction() -> str:
        rendered.append(True)
        if failure.startswith("rendering"):
            raise original_exception
        return " \n\t" if failure == "empty_instruction" else "Is this supported?"

    def on_started(event: StepStarted) -> None:
        started.append(event)
        if failure.startswith("started_delivery"):
            raise original_exception

    expected_exception = asyncio.CancelledError if failure.endswith("interrupted") else nh.ExecutionError
    with nh.run(executor), nh.scope(lifecycle=StepLifecycle(events.append, on_step_started=on_started)), pytest.raises(expected_exception) as caught:
        if asynchronous:
            await judge_async(instruction)
        else:
            judge(instruction)

    assert executor.calls == []
    assert rendered == ([] if failure.startswith("started_delivery") else [True])
    assert len(events) == len(started) == 1
    event = events[0]
    assert event.block_kind == "decide"
    assert event.execution_reference == started[0].execution_reference
    assert event.processed_natural_program is None
    assert event.input_binding_name_to_value is None
    assert event.allowed_outcome_kinds is None
    assert event.model_result is None
    assert event.assigned_binding_name_to_value == {}
    assert isinstance(event, (StepFailed, StepInterrupted))
    assert event.failure_stage == ("started_delivery" if failure.startswith("started_delivery") else "preparation")
    if failure == "empty_instruction":
        assert isinstance(event.original_exception, nh.NaturalParseError)
    else:
        assert event.original_exception is original_exception
    if isinstance(event, StepInterrupted):
        assert caught.value is original_exception
    else:
        assert caught.value.__cause__ is event.original_exception


@pytest.mark.parametrize("failure", ["namespace", "writer", "started_delivery", "started_delivery_interrupted"])
@pytest.mark.asyncio
async def test_embedded_decision_identity_survives_early_failure(execute: Callable[..., Awaitable[StepOutcome]], failure: str) -> None:
    definition = StepDefinition(
        "Is this supported?",
        block_kind="decide",
        output_binding_names=("result",),
        binding_name_to_type={"result": bool},
        source_location="decision_fixture:1",
    )
    events: list[StepFinished] = []
    executor = ScriptedExecutor([])
    original_exception = asyncio.CancelledError("interrupted") if failure.endswith("interrupted") else ValueError("unavailable")

    def on_started(event: StepStarted) -> None:
        if failure.startswith("started_delivery"):
            raise original_exception

    def write_binding(name: str, value: object) -> None:
        pytest.fail("Writer must not run")

    expected_exception = asyncio.CancelledError if failure.endswith("interrupted") else nh.ExecutionError
    with nh.run(executor), nh.scope(lifecycle=StepLifecycle(events.append, on_step_started=on_started)), pytest.raises(expected_exception):
        await execute(
            definition,
            name_to_value=None if failure == "namespace" else {},
            write_binding=None if failure == "writer" else write_binding,
        )

    assert executor.calls == []
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, (StepFailed, StepInterrupted))
    assert event.block_kind == "decide"
    assert event.execution_reference.source_location == definition.source_location
    assert event.failure_stage == ("started_delivery" if failure.startswith("started_delivery") else "preparation")
    assert event.processed_natural_program is None
    assert event.model_result is None
