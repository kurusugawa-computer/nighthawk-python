from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset

import nighthawk as nh
from nighthawk.lifecycle import StepFailed, StepFinished, StepLifecycle, StepRaised
from nighthawk.oversight import Accept, Oversight, OversightRejectedError, Reject
from nighthawk.runtime import (
    OperationError,
    OperationFinished,
    OperationRequest,
    Pass,
    StepCandidate,
    StepRequest,
    assign_tool_async,
    eval_expression_async,
)
from nighthawk.runtime.operation_contract import OperationDeliveryError
from nighthawk.tools.execution import ToolResultWrapperToolset
from tests.execution.stub_executor import StubExecutor


def new_context(**name_to_value: object) -> nh.StepContext:
    return nh.StepContext(
        execution_reference=nh.ExecutionReference("run", "scope", "step", "test"),
        step_globals={},
        step_locals=name_to_value,
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        implicit_reference_name_to_value={},
    )


def test_admission_failure_has_identity_and_prevents_inspection_and_effects() -> None:
    inspected: list[OperationRequest] = []
    finished: list[OperationFinished] = []
    effects: list[int] = []
    context = new_context(value=0, effects=effects)

    def inspect(request: OperationRequest) -> Accept:
        inspected.append(request)
        return Accept()

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=Oversight(inspect_operation=inspect), lifecycle=StepLifecycle(on_operation_finished=finished.append)),
        pytest.raises(OperationError),
    ):
        asyncio.run(assign_tool_async(context, "value", "effects.append(1)"))
    assert inspected == [] and effects == []
    assert len(finished) == 1
    assert finished[0].request.operation_id
    assert finished[0].request.execution_reference is context.execution_reference
    assert finished[0].status == "failed" and finished[0].phase == "admission"
    assert not finished[0].evaluation_started


def test_awaited_inspection_serializes_operations_in_admission_order() -> None:
    events: list[str] = []
    finished: list[OperationFinished] = []
    context = new_context()

    async def execute() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def inspect(request: OperationRequest) -> Accept:
            events.append(f"inspect:{request.kind}")
            if request.kind == "assignment":
                entered.set()
                await release.wait()
            return Accept()

        with nh.scope(oversight=Oversight(inspect_operation=inspect), lifecycle=StepLifecycle(on_operation_finished=finished.append)):
            first = asyncio.create_task(assign_tool_async(context, "i", "5"))
            await entered.wait()
            second = asyncio.create_task(eval_expression_async(context, "i"))
            await asyncio.sleep(0)
            assert events == ["inspect:assignment"]
            assert finished == []
            release.set()
            assignment, value = await asyncio.gather(first, second)
            assert assignment.value == value == 5

    with nh.run(StubExecutor()):
        asyncio.run(execute())
    assert events == ["inspect:assignment", "inspect:evaluation"]
    assert [event.status for event in finished] == ["succeeded", "succeeded"]
    assert len({event.request.operation_id for event in finished}) == 2


def test_rejection_stops_queued_operations_and_preserves_completed_effects() -> None:
    effects: list[str] = []
    finished: list[OperationFinished] = []
    context = new_context(effects=effects)

    async def execute() -> None:
        inspected = asyncio.Event()
        release = asyncio.Event()

        async def inspect(request: OperationRequest) -> Accept | Reject:
            if request.expression == "effects.append('reject')":
                inspected.set()
                await release.wait()
                return Reject("denied")
            return Accept()

        with nh.scope(oversight=Oversight(inspect_operation=inspect), lifecycle=StepLifecycle(on_operation_finished=finished.append)):
            await eval_expression_async(context, "effects.append('approved')")
            first = asyncio.create_task(eval_expression_async(context, "effects.append('reject')"))
            await inspected.wait()
            second = asyncio.create_task(eval_expression_async(context, "effects.append('queued')"))
            await asyncio.sleep(0)
            release.set()
            outcomes = await asyncio.gather(first, second, return_exceptions=True)
            assert all(isinstance(outcome, OversightRejectedError) for outcome in outcomes)

    with nh.run(StubExecutor()):
        asyncio.run(execute())
    assert effects == ["approved"]
    assert [event.status for event in finished] == ["succeeded", "rejected"]
    assert not finished[-1].evaluation_started


def test_operation_notification_failure_keeps_successful_fact_and_stops_step() -> None:
    recorded: list[OperationFinished] = []
    storage_error = RuntimeError("storage unavailable")
    context = new_context()

    async def finished(event: OperationFinished) -> None:
        recorded.append(event)
        await asyncio.sleep(0)
        raise storage_error

    async def execute() -> None:
        with pytest.raises(OperationDeliveryError) as caught:
            await assign_tool_async(context, "value", "7")
        assert caught.value.finished is recorded[0]
        assert caught.value.delivery_cause is storage_error
        assert context.environment.resolve("value") == 7
        with pytest.raises(OperationDeliveryError):
            await assign_tool_async(context, "next_value", "8")
        assert "next_value" not in context.environment.step_name_to_value

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(on_operation_finished=finished)):
        asyncio.run(execute())
    assert len(recorded) == 1 and recorded[0].status == "succeeded"


def test_failed_operation_and_failed_notification_retain_both_causes() -> None:
    body_error = ValueError("body failed")
    storage_error = RuntimeError("storage failed")

    def fail() -> None:
        raise body_error

    def finished(event: OperationFinished) -> None:
        raise storage_error

    context = new_context(fail=fail)
    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(on_operation_finished=finished)), pytest.raises(OperationDeliveryError) as caught:
        asyncio.run(eval_expression_async(context, "fail()"))
    assert caught.value.finished.original_exception is body_error
    assert caught.value.__cause__ is body_error
    assert caught.value.delivery_cause is storage_error


def test_cancelled_approval_never_starts_pending_body() -> None:
    effects: list[int] = []
    recorded: list[OperationFinished] = []

    async def execute() -> None:
        entered = asyncio.Event()

        async def inspect(request: OperationRequest) -> Accept:
            entered.set()
            await asyncio.Event().wait()
            return Accept()

        with nh.scope(oversight=Oversight(inspect_operation=inspect), lifecycle=StepLifecycle(on_operation_finished=recorded.append)):
            operation = asyncio.create_task(eval_expression_async(new_context(effects=effects), "effects.append(1)"))
            await entered.wait()
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation

    with nh.run(StubExecutor()):
        asyncio.run(execute())
    assert effects == []
    assert len(recorded) == 1 and recorded[0].status == "interrupted"
    assert recorded[0].phase == "inspection" and not recorded[0].evaluation_started


def test_child_raise_propagates_and_records_distinct_parent_and_child_steps() -> None:
    terminal: list[StepFinished] = []
    operations: list[OperationFinished] = []

    @nh.natural_function
    async def child() -> int:
        """natural
        {"step_outcome": {"kind": "raise", "raise_message": "child failed", "raise_error_type": "ValueError"}, "bindings": {}}
        """
        return 0

    @nh.natural_function
    async def parent() -> int:
        nonlocal child
        """natural
        {"step_outcome": {"kind": "return", "return_expression": "child()"}, "bindings": {}}
        """
        return 0

    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=terminal.append, on_operation_finished=operations.append)),
        pytest.raises(ValueError) as caught,
    ):
        asyncio.run(parent())
    assert len(terminal) == 2
    assert isinstance(terminal[0], StepRaised) and isinstance(terminal[1], StepFailed)
    assert terminal[0].exception is terminal[1].original_exception is caught.value
    assert terminal[0].execution_reference.parent_step_execution_id == terminal[1].execution_reference.step_execution_id
    assert operations[-1].original_exception is caught.value
    assert operations[0].value is ValueError


def test_return_expression_and_async_commit_inspection_do_not_replay_effects() -> None:
    effects: list[str] = []
    operations: list[OperationFinished] = []
    commits: list[nh.oversight.StepCommit] = []

    @nh.natural_function
    async def workflow() -> int:
        nonlocal effects
        """natural
        {"step_outcome": {"kind": "return", "return_expression": "(effects.append('executed'), 7)[1]"}, "bindings": {}}
        """
        return 0

    async def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.Rewrite:
        commits.append(commit)
        await asyncio.sleep(0)
        return nh.oversight.Rewrite(outcome=nh.oversight.Return(9))

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=Oversight(inspect_step_commit=inspect), lifecycle=StepLifecycle(on_operation_finished=operations.append)),
    ):
        assert asyncio.run(workflow()) == 9
    assert effects == ["executed"] and len(commits) == len(operations) == 1
    assert operations[0].request.kind == "return" and operations[0].value == 7


def test_native_tool_uses_the_same_operation_inspection_and_live_argument_record() -> None:
    requests: list[OperationRequest] = []
    operations: list[OperationFinished] = []

    async def native(value: int) -> int:
        return value + 1

    def inspect(request: OperationRequest) -> Accept:
        requests.append(request)
        return Accept()

    async def execute() -> None:
        agent = Agent(TestModel(call_tools="all", custom_output_text="done"), deps_type=nh.StepContext)
        await agent.run("Run native", deps=new_context(), toolsets=[ToolResultWrapperToolset(FunctionToolset([native]))])

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=Oversight(inspect_operation=inspect), lifecycle=StepLifecycle(on_operation_finished=operations.append)),
    ):
        asyncio.run(execute())
    assert len(requests) == len(operations) == 1
    assert requests[0].kind == "native_tool" and requests[0].tool_name == "native"
    assert operations[0].request is requests[0]
    assert operations[0].value == requests[0].argument_name_to_value["value"] + 1  # type: ignore[operator]


def test_explicit_pydantic_retry_does_not_poison_step() -> None:
    calls = 0

    def retry_once() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelRetry("try again")
        return 7

    context = new_context(retry_once=retry_once)

    async def execute() -> None:
        with pytest.raises(ModelRetry):
            await eval_expression_async(context, "retry_once()")
        assert await eval_expression_async(context, "retry_once()") == 7

    asyncio.run(execute())


def test_executor_cannot_catch_rejection_and_finalize_a_candidate() -> None:
    terminal: list[StepFinished] = []

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            with pytest.raises(OversightRejectedError):
                await eval_expression_async(request.step_context, "7")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"value": 9})

    @nh.natural_function
    async def workflow() -> int:
        value: int = 0
        """natural
        Set <:value>.
        """
        return value

    with (
        nh.run(Executor()),
        nh.scope(oversight=Oversight(inspect_operation=lambda request: Reject("denied")), lifecycle=StepLifecycle(terminal.append)),
        pytest.raises(OversightRejectedError),
    ):
        asyncio.run(workflow())
    assert len(terminal) == 1 and isinstance(terminal[0], StepFailed)
    assert terminal[0].assigned_binding_name_to_value == {}
