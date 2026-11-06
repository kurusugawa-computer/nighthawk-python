from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.tools.execution import ToolResultWrapperToolset
from nighthawk.core import oversight
from nighthawk.core.assignment import assign_expression_async
from nighthawk.core.errors import ExecutionError
from nighthawk.core.execution_reference import ExecutionReference
from nighthawk.core.expression import evaluate_expression_async
from nighthawk.core.lifecycle import StepFailed, StepFinished, StepLifecycle, StepRaised
from nighthawk.core.operation_contract import OperationDeliveryError, OperationError, OperationFinished, OperationRequest, TerminalOperationError
from nighthawk.core.operations import failed_operation
from nighthawk.core.oversight import AbortStep, Accept, Oversight, StepAbortedError
from nighthawk.core.step_context import StepContext
from nighthawk.core.step_outcome import Pass, Raise, Return
from nighthawk.core.step_request import StepCandidate, StepRequest
from tests.support.contexts import new_context
from tests.support.executors import StubExecutor


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
        asyncio.run(assign_expression_async(context, "value", "effects.append(1)"))
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
            first = asyncio.create_task(assign_expression_async(context, "i", "5"))
            await entered.wait()
            second = asyncio.create_task(evaluate_expression_async(context, "i"))
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

        async def inspect(request: OperationRequest) -> Accept | AbortStep:
            if request.expression == "effects.append('reject')":
                inspected.set()
                await release.wait()
                return AbortStep("denied")
            return Accept()

        with nh.scope(oversight=Oversight(inspect_operation=inspect), lifecycle=StepLifecycle(on_operation_finished=finished.append)):
            await evaluate_expression_async(context, "effects.append('approved')")
            first = asyncio.create_task(evaluate_expression_async(context, "effects.append('reject')"))
            await inspected.wait()
            second = asyncio.create_task(evaluate_expression_async(context, "effects.append('queued')"))
            await asyncio.sleep(0)
            release.set()
            outcomes = await asyncio.gather(first, second, return_exceptions=True)
            assert all(isinstance(outcome, StepAbortedError) for outcome in outcomes)

    with nh.run(StubExecutor()):
        asyncio.run(execute())
    assert effects == ["approved"]
    assert [event.status for event in finished] == ["succeeded", "aborted"]
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
            await assign_expression_async(context, "value", "7")
        assert caught.value.finished is recorded[0]
        assert caught.value.delivery_cause is storage_error
        assert context.environment.resolve("value") == 7
        with pytest.raises(OperationDeliveryError):
            await assign_expression_async(context, "next_value", "8")
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
        asyncio.run(evaluate_expression_async(context, "fail()"))
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
            operation = asyncio.create_task(evaluate_expression_async(new_context(effects=effects), "effects.append(1)"))
            await entered.wait()
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation

    with nh.run(StubExecutor()):
        asyncio.run(execute())
    assert effects == []
    assert len(recorded) == 1 and recorded[0].status == "interrupted"
    assert recorded[0].phase == "inspection" and not recorded[0].evaluation_started


def test_child_raise_fails_the_parent_operation_and_records_distinct_steps() -> None:
    terminal: list[StepFinished] = []
    operations: list[OperationFinished] = []

    @nh.function
    async def child() -> int:
        """execute
        {"step_outcome": {"kind": "raise", "raise_message": "child failed", "raise_error_type": "ValueError"}, "bindings": {}}
        """
        return 0

    @nh.function
    async def parent() -> int:
        nonlocal child
        """execute
        {"step_outcome": {"kind": "return", "return_expression": "child()"}, "bindings": {}}
        """
        return 0

    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=terminal.append, on_operation_finished=operations.append)),
        pytest.raises(ExecutionError) as caught,
    ):
        asyncio.run(parent())
    assert len(terminal) == 2
    assert isinstance(terminal[0], StepRaised) and isinstance(terminal[1], StepFailed)
    # The stub executor does not recover the failed return operation, so the parent step fails with the child's exception.
    assert terminal[0].exception is terminal[1].original_exception is caught.value.__cause__
    assert terminal[0].execution_reference.parent_step_execution_id == terminal[1].execution_reference.step_execution_id
    assert operations[-1].status == "failed" and operations[-1].original_exception is terminal[0].exception
    assert terminal[1].failed_operation_tuple == (operations[-1],)
    assert operations[0].value is ValueError


def test_return_expression_and_async_commit_inspection_do_not_replay_effects() -> None:
    effects: list[str] = []
    operations: list[OperationFinished] = []
    commits: list[oversight.StepCommit] = []

    @nh.function
    async def workflow() -> int:
        nonlocal effects
        """execute
        {"step_outcome": {"kind": "return", "return_expression": "(effects.append('executed'), 7)[1]"}, "bindings": {}}
        """
        return 0

    async def inspect(commit: oversight.StepCommit) -> oversight.Rewrite:
        commits.append(commit)
        await asyncio.sleep(0)
        return oversight.Rewrite(outcome=Return(9))

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
        agent = Agent(TestModel(call_tools="all", custom_output_text="done"), deps_type=StepContext)
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


@pytest.mark.parametrize("native_tool", [False, True])
@pytest.mark.parametrize("asynchronous_helper", [False, True])
@pytest.mark.parametrize("reuse_diagnostic", [False, True])
async def test_application_exception_fails_only_its_operation(native_tool: bool, asynchronous_helper: bool, reuse_diagnostic: bool) -> None:
    context = new_context()
    if reuse_diagnostic:
        with pytest.raises(OperationError) as caught:
            await evaluate_expression_async(context, "missing")
        error: Exception = caught.value
    else:
        error = ValueError("application failed")
    effects: list[str] = []
    operations: list[OperationFinished] = []

    def fail() -> int:
        effects.append("entered")
        raise error

    async def fail_async() -> int:
        await asyncio.sleep(0)
        return fail()

    helper = fail_async if asynchronous_helper else fail
    context.environment.assign("fail", helper)
    context.environment.assign("effects", effects)
    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(on_operation_finished=operations.append)):
        if native_tool:
            agent = Agent(TestModel(call_tools="all", custom_output_text="done"), deps_type=StepContext)
            result = await agent.run("Run the tool", deps=context, toolsets=[ToolResultWrapperToolset(FunctionToolset([helper]))])
            assert result.output == "done"
        else:
            with pytest.raises(type(error)) as caught_failure:
                await evaluate_expression_async(context, "fail()")
            assert caught_failure.value is error
        await evaluate_expression_async(context, "effects.append('continued')")
    assert effects == ["entered", "continued"]
    assert [operation.status for operation in operations] == ["failed", "succeeded"]
    assert operations[0].original_exception is error and operations[0].evaluation_started
    assert failed_operation(context, operation_id=operations[0].request.operation_id) is operations[0]


async def test_terminal_operation_error_ends_the_step() -> None:
    class IntegrityError(TerminalOperationError):
        pass

    error = IntegrityError("ledger mismatch")
    effects: list[str] = []

    def fail() -> int:
        raise error

    context = new_context(fail=fail, effects=effects)
    with nh.run(StubExecutor()):
        with pytest.raises(IntegrityError):
            await evaluate_expression_async(context, "fail()")
        with pytest.raises(IntegrityError) as repeated:
            await evaluate_expression_async(context, "effects.append('continued')")
    assert repeated.value is error and effects == []
    assert failed_operation(context, exception=error) is None


@pytest.mark.parametrize("abort", [False, True])
def test_executor_cannot_suppress_terminal_operation_failure(abort: bool) -> None:
    terminal: list[StepFinished] = []
    error = TerminalOperationError("helper ended the step")

    def fail() -> None:
        raise error

    error_type = StepAbortedError if abort else TerminalOperationError

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            with pytest.raises(error_type):
                await evaluate_expression_async(request.step_context, "7" if abort else "fail()")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"value": 9})

    @nh.function
    async def workflow() -> int:
        value: int = 0
        """execute
        Set <:value>.
        """
        return value

    with (
        nh.run(Executor()),
        nh.scope(
            task_references={"fail": fail},
            oversight=Oversight(inspect_operation=lambda request: AbortStep("denied")) if abort else None,
            lifecycle=StepLifecycle(on_step_finished=terminal.append),
        ),
        pytest.raises(error_type) as caught,
    ):
        asyncio.run(workflow())
    assert len(terminal) == 1 and isinstance(terminal[0], StepFailed)
    assert terminal[0].assigned_binding_name_to_value == {}
    assert terminal[0].original_exception is caught.value
    if not abort:
        assert caught.value is error
        assert terminal[0].failure_stage == "operation_execution" and terminal[0].failed_operation_tuple == ()


def test_request_freezes_arguments_mapping_but_preserves_values() -> None:
    values = [1]
    tool_call = OperationRequest(
        execution_reference=ExecutionReference("run", "scope", "step"),
        operation_id="operation",
        kind="native_tool",
        tool_name="lookup",
        argument_name_to_value={"values": values},
    )
    assert tool_call.argument_name_to_value["values"] is values

    with pytest.raises(TypeError):
        tool_call.argument_name_to_value["values"] = []  # type: ignore[index]


@pytest.mark.parametrize("decision", ["reraise", "abort_commit"])
def test_recovered_failure_is_visible_to_commit_inspection_and_can_be_reraised(decision: str) -> None:
    error = ValueError("helper failed")
    commits: list[oversight.StepCommit] = []
    terminal: list[StepFinished] = []

    def fail() -> None:
        raise error

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            with pytest.raises(ValueError):
                await evaluate_expression_async(request.step_context, "fail()")
            finished = failed_operation(request.step_context, exception=error)
            assert finished is not None
            return StepCandidate(outcome=Raise.of(error) if decision == "reraise" else Pass())

    def inspect(commit: oversight.StepCommit) -> oversight.StepCommitDecision:
        commits.append(commit)
        return AbortStep("unresolved failure") if commit.failed_operation_tuple and decision == "abort_commit" else oversight.Accept()

    @nh.function
    async def workflow() -> None:
        """execute
        Call fail.
        """

    with (
        nh.run(Executor()),
        nh.scope(
            task_references={"fail": fail},
            oversight=Oversight(inspect_step_commit=inspect),
            lifecycle=StepLifecycle(on_step_finished=terminal.append),
        ),
        pytest.raises(ValueError if decision == "reraise" else StepAbortedError) as caught,
    ):
        asyncio.run(workflow())
    assert len(commits) == 1 and [operation.original_exception for operation in commits[0].failed_operation_tuple] == [error]
    assert terminal[0].failed_operation_tuple == commits[0].failed_operation_tuple
    if decision == "reraise":
        assert caught.value is error
        assert isinstance(terminal[0], StepRaised) and terminal[0].exception is error


def test_raise_exception_must_match_its_error_type() -> None:
    with pytest.raises(TypeError, match="exactly error_type"):
        Raise("failed", Exception, exception=ValueError("failed"))
    error = KeyError("missing")
    assert Raise.of(error).exception is error and Raise.of(error).error_type is KeyError
