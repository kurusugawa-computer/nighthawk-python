from __future__ import annotations

import asyncio
import faulthandler
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepStarted
from nighthawk.runtime import OperationFinished, Pass, StepCandidate, StepRequest, eval_expression
from nighthawk.runtime.notifications import await_notification
from tests.support.executors import StubExecutor


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

    @nh.function
    def synchronous() -> int:
        value = 0
        """execute
        Set <:value>.
        """
        return value

    @nh.function
    async def asynchronous_function() -> int:
        value = 0
        """execute
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

    @nh.function
    async def workflow() -> None:
        """execute
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

    @nh.function
    async def workflow() -> int:
        value = 0
        try:
            """execute
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

        @nh.function
        async def workflow() -> None:
            """execute
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

        @nh.function
        async def workflow() -> None:
            """execute
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

        @nh.function
        def workflow() -> None:
            """execute
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


@pytest.mark.parametrize("inside_event_loop", [False, True])
@pytest.mark.parametrize("operation_notification", [False, True])
def test_interrupted_sync_notification_joins_cleanup_before_return(inside_event_loop: bool, operation_notification: bool) -> None:
    _run_completion_scenario("synchronous", str(inside_event_loop), str(operation_notification))


def _run_completion_scenario(*arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tests.lifecycle.test_notifications", *arguments],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Task was destroyed" not in result.stderr
    assert "was never awaited" not in result.stderr
    assert "Exception in thread" not in result.stderr


def _exercise_interrupted_sync_notification(inside_event_loop: bool, operation_notification: bool) -> None:
    original = asyncio.CancelledError("original interruption")
    notifications: list[StepFinished | OperationFinished] = []
    cleanup_tasks: list[asyncio.Task[object]] = []
    cleanup_entered = threading.Event()
    release = threading.Event()
    cleanup_finished = threading.Event()

    def interrupt() -> None:
        raise original

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            if operation_notification:
                eval_expression(request.step_context, "interrupt()")
            raise original

    async def finished(event: StepFinished | OperationFinished) -> None:
        notifications.append(event)
        task = asyncio.current_task()
        assert task is not None
        cleanup_tasks.append(task)
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cleanup_entered.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            cleanup_finished.set()

    @nh.function
    def workflow() -> None:
        nonlocal interrupt
        """execute
        Interrupt this step.
        """

    def invoke() -> None:
        with (
            nh.run(Executor()),
            nh.scope(
                lifecycle=StepLifecycle(
                    on_step_finished=None if operation_notification else finished,
                    on_operation_finished=finished if operation_notification else None,
                    interruption_delivery_timeout=0.02,
                )
            ),
            pytest.raises(asyncio.CancelledError) as caught,
        ):
            workflow()
        assert caught.value is original

    async def invoke_inside_loop() -> None:
        invoke()

    def release_cleanup() -> None:
        assert cleanup_entered.wait(3)
        assert not cleanup_finished.is_set()
        release.set()

    controller = threading.Thread(target=release_cleanup)
    controller.start()
    try:
        if inside_event_loop:
            asyncio.run(invoke_inside_loop())
        else:
            invoke()
        assert cleanup_finished.is_set()
        assert len(notifications) == 1
        assert isinstance(notifications[0], (StepInterrupted, OperationFinished))
        if isinstance(notifications[0], OperationFinished):
            assert notifications[0].status == "interrupted"
        assert notifications[0].original_exception is original
        assert cleanup_tasks[0].done()
        assert cleanup_tasks[0].get_loop().is_closed()
    finally:
        release.set()
        controller.join()


@pytest.mark.parametrize("boundary", ["started", "finished", "operation"])
@pytest.mark.parametrize("self_cancel", [False, True])
def test_repeated_async_cancellation_joins_owned_notification(boundary: str, self_cancel: bool) -> None:
    _run_completion_scenario("asynchronous", boundary, str(self_cancel))


def _exercise_repeated_async_cancellation(boundary: str, self_cancel: bool) -> None:
    async def execute() -> None:
        entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        effects: list[str] = []
        original: list[BaseException] = []

        async def notification(event: object) -> None:
            entered.set()
            if self_cancel:
                current = asyncio.current_task()
                assert current is not None
                current.cancel("notification cancellation")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exception:
                original.append(exception)
                cleanup.set()
                await release.wait()
                effects.append("cleaned")

        class Executor:
            async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
                if boundary == "operation":
                    await nh.runtime.eval_expression_async(request.step_context, "1")
                return StepCandidate(outcome=Pass(), binding_name_to_value={})

        @nh.function
        async def workflow() -> None:
            """execute
            Complete this block.
            """

        lifecycle = StepLifecycle(
            on_step_started=notification if boundary == "started" else None,
            on_step_finished=notification if boundary == "finished" else None,
            on_operation_finished=notification if boundary == "operation" else None,
        )
        with nh.run(Executor()), nh.scope(lifecycle=lifecycle):
            task = asyncio.create_task(workflow())
            try:
                await entered.wait()
                if self_cancel:
                    await cleanup.wait()
                task.cancel("first")
                await cleanup.wait()
                task.cancel("second")
                for _ in range(5):
                    await asyncio.sleep(0)
                assert not task.done()
                assert effects == []
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert str(caught.value) == "first"
            assert effects == ["cleaned"]
            assert len(original) == 1

    asyncio.run(execute())


def test_notification_timeout_preserves_existing_cancellation_cleanup() -> None:
    _run_completion_scenario("timeout")


def _exercise_notification_timeout() -> None:
    async def execute() -> None:
        cleanup, release = asyncio.Event(), asyncio.Event()
        effects: list[str] = []
        cleanup_error = RuntimeError("notification cleanup failed")

        async def notification() -> None:
            current = asyncio.current_task()
            assert current is not None
            current.cancel("notification cancellation")
            try:
                await asyncio.sleep(0)
            finally:
                cleanup.set()
                await release.wait()
                effects.append("cleaned")
                raise cleanup_error

        task = asyncio.create_task(await_notification(notification(), interrupted=True, timeout=0.01))
        try:
            await cleanup.wait()
            await asyncio.sleep(0.05)
            assert not task.done()
            assert effects == []
        finally:
            release.set()
            with pytest.raises(TimeoutError) as caught:
                await task
        assert effects == ["cleaned"]
        cause = caught.value.__cause__
        assert isinstance(cause, BaseExceptionGroup)
        assert cleanup_error in cause.exceptions
        assert any(isinstance(exception, asyncio.CancelledError) for exception in cause.exceptions)

    asyncio.run(execute())


if __name__ == "__main__":
    faulthandler.dump_traceback_later(10)
    try:
        if sys.argv[1] == "synchronous":
            _exercise_interrupted_sync_notification(sys.argv[2] == "True", sys.argv[3] == "True")
        elif sys.argv[1] == "timeout":
            _exercise_notification_timeout()
        else:
            _exercise_repeated_async_cancellation(sys.argv[2], sys.argv[3] == "True")
    finally:
        faulthandler.cancel_dump_traceback_later()
