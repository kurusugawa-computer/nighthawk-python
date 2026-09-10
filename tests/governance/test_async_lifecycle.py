from __future__ import annotations

import asyncio

import pytest

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepStarted
from nighthawk.runtime import Pass, StepCandidate, StepRequest
from tests.execution.stub_executor import StubExecutor


@pytest.mark.parametrize("asynchronous", [False, True])
def test_async_notifications_are_awaited_in_order(asynchronous: bool) -> None:
    events: list[str] = []
    identities: list[nh.ExecutionReference] = []

    async def started(event: StepStarted) -> None:
        assert nh.get_execution_reference() == event.execution_reference
        identities.append(event.execution_reference)
        events.append("starting")
        await asyncio.sleep(0)
        events.append("started")

    async def finished(event: StepFinished) -> None:
        assert isinstance(event, StepCompleted)
        assert nh.get_execution_reference() == event.execution_reference
        identities.append(event.execution_reference)
        assert event.assigned_binding_name_to_value == {"value": 7}
        await asyncio.sleep(0)
        events.append("finished")

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            events.append("executed")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"value": 7})

    @nh.natural_function
    def synchronous() -> int:
        value = 0
        """natural
        Set <:value>.
        """
        return value

    @nh.natural_function
    async def asynchronous_function() -> int:
        value = 0
        """natural
        Set <:value>.
        """
        return value

    with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(on_step_started=started, on_step_finished=finished)):
        assert (asyncio.run(asynchronous_function()) if asynchronous else synchronous()) == 7
    assert events == ["starting", "started", "executed", "finished"]
    assert identities[0] == identities[1]


def test_started_delivery_failure_prevents_preparation_and_still_delivers_terminal() -> None:
    events: list[StepFinished] = []
    error = ValueError("start storage failed")

    async def started(event: StepStarted) -> None:
        await asyncio.sleep(0)
        raise error

    @nh.natural_function
    async def workflow() -> None:
        """natural
        <missing_preparation_input>
        """

    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_started=started, on_step_finished=events.append)),
        pytest.raises(nh.ExecutionError) as caught,
    ):
        asyncio.run(workflow())
    assert caught.value.__cause__ is error
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "started_delivery"
    assert events[0].processed_natural_program is None
    assert events[0].execution_reference.step_execution_id


def test_terminal_delivery_failure_preserves_caller_writes_and_completed_record() -> None:
    events: list[StepFinished] = []
    error = RuntimeError("terminal storage failed")

    async def finished(event: StepFinished) -> None:
        events.append(event)
        await asyncio.sleep(0)
        raise error

    @nh.natural_function
    async def workflow() -> int:
        value = 0
        try:
            """natural
            <:value>
            {"step_outcome": {"kind": "pass"}, "bindings": {"value": 7}}
            """
        except RuntimeError:
            return value
        return 0

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(on_step_finished=finished)):
        assert asyncio.run(workflow()) == 7
    assert len(events) == 1 and isinstance(events[0], StepCompleted)
    assert events[0].assigned_binding_name_to_value == {"value": 7}


@pytest.mark.parametrize("repeated_cancellation", [False, True])
def test_interruption_bounds_async_terminal_cleanup_and_preserves_original(repeated_cancellation: bool) -> None:
    events: list[StepFinished] = []

    async def execute() -> None:
        entered = asyncio.Event()
        cleanup_entered = asyncio.Event()

        class Executor:
            async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
                entered.set()
                await asyncio.Event().wait()
                return StepCandidate(outcome=Pass(), binding_name_to_value={})

        async def finished(event: StepFinished) -> None:
            events.append(event)
            cleanup_entered.set()
            await asyncio.Event().wait()

        @nh.natural_function
        async def workflow() -> None:
            """natural
            Wait.
            """

        with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(on_step_finished=finished, interruption_delivery_timeout=0.02)):
            task = asyncio.create_task(workflow())
            await entered.wait()
            task.cancel("original cancellation")
            await cleanup_entered.wait()
            if repeated_cancellation:
                task.cancel("later cancellation")
            with pytest.raises(asyncio.CancelledError) as caught:
                await asyncio.wait_for(task, timeout=1)
            assert len(events) == 1 and isinstance(events[0], StepInterrupted)
            assert caught.value is events[0].original_exception
            assert str(caught.value) == "original cancellation"

    asyncio.run(execute())


def test_async_source_hooks_share_the_callers_event_loop() -> None:
    async def execute() -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def finished(event: StepFinished) -> None:
            asyncio.get_running_loop().call_soon(future.set_result, None)
            await future

        @nh.natural_function
        async def workflow() -> None:
            """natural
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
            """

        with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(on_step_finished=finished)):
            await workflow()
        assert future.done()

    asyncio.run(execute())


def test_sync_bridge_reports_a_foreign_event_loop_resource() -> None:
    async def execute() -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def started(event: StepStarted) -> None:
            await future

        @nh.natural_function
        def workflow() -> None:
            """natural
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
            """

        try:
            with (
                nh.run(StubExecutor()),
                nh.scope(lifecycle=StepLifecycle(on_step_started=started)),
                pytest.raises(nh.ExecutionError, match="different loop"),
            ):
                workflow()
        finally:
            future.cancel()

    asyncio.run(execute())
