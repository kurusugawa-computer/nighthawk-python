from __future__ import annotations

import ast
import asyncio
import inspect
import json
from typing import Annotated

import pytest
from pydantic import AfterValidator

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepRaised
from nighthawk.natural.transform import build_runtime_call_and_assignments
from nighthawk.runtime.runner import Runner
from nighthawk.runtime.step_contract import PassStepOutcome, StepKind, StepOutcome
from nighthawk.testing import ScriptedExecutor, StepResponse, return_response
from tests.execution.stub_executor import StubExecutor


@pytest.mark.parametrize("asynchronous", [False, True])
def test_completion_runs_after_caller_assignments(asynchronous: bool) -> None:
    value = 1
    events: list[StepFinished] = []

    def finished(event: StepFinished) -> None:
        assert value == 7
        assert event.assigned_binding_name_to_value == {"value": 7}
        assert nh.get_execution_reference() == event.execution_reference
        assert nh.get_step_context().execution_reference == event.execution_reference
        events.append(event)

    @nh.natural_function
    def synchronous() -> None:
        nonlocal value
        """natural
        <:value>
        {"step_outcome": {"kind": "pass"}, "bindings": {"value": 7}}
        """

    @nh.natural_function
    async def asynchronous_function() -> None:
        nonlocal value
        """natural
        <:value>
        {"step_outcome": {"kind": "pass"}, "bindings": {"value": 7}}
        """

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(finished)):
        asyncio.run(asynchronous_function()) if asynchronous else synchronous()
        assert nh.get_execution_reference().step_execution_id is None
    assert len(events) == 1 and isinstance(events[0], StepCompleted)


@pytest.mark.parametrize("outcome", ["pass", "return", "break", "continue"])
def test_delivery_exception_does_not_reenter_completion(outcome: str) -> None:
    value = 1
    events: list[StepFinished] = []
    outcome_json = json.dumps({"kind": outcome, **({"return_expression": "9"} if outcome == "return" else {})})

    def finished(event: StepFinished) -> None:
        events.append(event)
        raise RuntimeError("delivery")

    @nh.natural_function
    def workflow() -> int:
        nonlocal value
        for _ in range(2):
            f"""natural
            <:value>
            {{"step_outcome": {outcome_json}, "bindings": {{"value": 7}}}}
            """
        return 0

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(finished)), pytest.raises(RuntimeError, match="delivery"):
        workflow()
    assert value == 7
    assert len(events) == 1 and isinstance(events[0], StepCompleted)


def test_return_expression_rejection_prevents_evaluation() -> None:
    evaluated: list[str] = []
    events: list[StepFinished] = []

    def calculate() -> int:
        evaluated.append("evaluated")
        return 3

    @nh.natural_function
    def workflow() -> int:
        """natural
        <calculate> <:value>
        {"step_outcome": {"kind": "return", "return_expression": "calculate()"}, "bindings": {"value": "7"}}
        """
        return 0

    def reject(request: nh.oversight.ReturnExpression) -> nh.oversight.Reject:
        assert request.expression == "calculate()"
        assert request.expected_type is int
        assert request.validated_binding_name_to_value == {"value": "7"}
        assert request.execution_reference == nh.get_execution_reference()
        return nh.oversight.Reject("return forbidden")

    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(events.append), oversight=nh.oversight.Oversight(inspect_return_expression=reject)),
        pytest.raises(nh.ExecutionError) as caught,
    ):
        workflow()
    assert evaluated == []
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StepFailed)
    assert event.failure_stage == "oversight_rejection" and event.inspection_subject == "return_expression"
    assert event.rejection_reason == "return forbidden"
    assert caught.value.step_failed is event
    assert caught.value.__cause__ is event.original_exception
    assert not event.assigned_binding_name_to_value


def test_return_expression_acceptance_evaluates_and_awaits_once() -> None:
    calls: list[str] = []

    def validate(value: int) -> int:
        calls.append("validated")
        return value

    Counted = Annotated[int, AfterValidator(validate)]  # noqa: N806

    async def calculate() -> int:
        calls.append("awaited")
        return 4

    def expression() -> object:
        calls.append("evaluated")
        return calculate()

    @nh.natural_function
    async def workflow() -> Counted:  # type: ignore[valid-type]
        """natural
        <expression> <Counted> <:value>
        {"step_outcome": {"kind": "return", "return_expression": "expression()"}, "bindings": {"value": 1}}
        """
        return 0

    def inspect_expression(request: nh.oversight.ReturnExpression) -> nh.oversight.Accept:
        calls.append("approved")
        return nh.oversight.Accept()

    def finished(event: StepFinished) -> None:
        calls.append("finished")
        assert isinstance(event, StepCompleted)
        assert event.outcome == nh.oversight.Return(4)
        assert event.assigned_binding_name_to_value == {"value": 9}

    with (
        nh.run(StubExecutor()),
        nh.scope(
            lifecycle=StepLifecycle(finished),
            oversight=nh.oversight.Oversight(
                inspect_return_expression=inspect_expression,
                inspect_step_commit=lambda commit: nh.oversight.Rewrite(binding_name_to_value={"value": 9}),
            ),
        ),
    ):
        assert asyncio.run(workflow()) == 4
    assert calls == ["approved", "evaluated", "awaited", "validated", "finished"]


def test_execution_identity_distinguishes_repeated_and_concurrent_calls() -> None:
    events: list[StepFinished] = []
    identities: list[nh.ExecutionReference] = []

    class Executor:
        async def run_step_async(
            self, *, processed_natural_program: str, step_context: nh.StepContext, binding_names: list[str], allowed_step_kinds: tuple[StepKind, ...]
        ) -> tuple[StepOutcome, dict[str, object]]:
            identities.append(step_context.execution_reference)
            await asyncio.sleep(0)
            assert nh.get_execution_reference() == step_context.execution_reference
            return PassStepOutcome(kind="pass"), {}

    @nh.natural_function
    async def workflow() -> None:
        """natural
        Finish.
        """

    async def execute() -> None:
        await workflow()
        await workflow()
        await asyncio.gather(workflow(), workflow())

    with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(events.append)):
        asyncio.run(execute())
    assert len(events) == 4
    assert len({event.execution_reference.step_execution_id for event in events}) == 4
    assert len({event.execution_reference.source_location for event in events}) == 1
    assert {event.execution_reference for event in events} == set(identities)


@pytest.mark.parametrize("delivery_fails", [False, True])
def test_cancellation_preserves_interruption_and_delivers_once(delivery_fails: bool) -> None:
    events: list[StepFinished] = []
    delivery_exception = RuntimeError("ledger unavailable")

    def finished(event: StepFinished) -> None:
        events.append(event)
        if delivery_fails:
            raise delivery_exception

    async def blocked() -> int:
        await asyncio.Event().wait()
        return 0

    @nh.natural_function
    async def workflow() -> int:
        """natural
        <blocked>
        {"step_outcome": {"kind": "return", "return_expression": "blocked()"}, "bindings": {}}
        """
        return 0

    async def execute() -> None:
        task = asyncio.create_task(workflow())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, StepInterrupted)
        assert event.failure_stage == "return_await"
        assert event.original_exception is caught.value
        if delivery_fails:
            assert caught.value.__cause__ is delivery_exception
        assert nh.get_execution_reference().step_execution_id is None

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(finished)):
        asyncio.run(execute())


def test_raise_constructor_failure_is_not_domain_raise() -> None:
    events: list[StepFinished] = []
    failure = ValueError("constructor failed")

    class DomainError(nh.NighthawkError):
        def __init__(self, message: str) -> None:
            raise failure

    @nh.natural_function
    def workflow() -> None:
        """natural
        <DomainError>
        {"step_outcome": {"kind": "raise", "raise_message": "domain", "raise_error_type": "DomainError"}, "bindings": {}}
        """

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(events.append)), pytest.raises(nh.ExecutionError) as caught:
        workflow()
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StepFailed) and event.failure_stage == "raise_construction"
    assert event.original_exception is failure is caught.value.__cause__


def test_domain_nighthawk_error_is_a_raised_record() -> None:
    events: list[StepFinished] = []

    class DomainError(nh.NighthawkError):
        pass

    @nh.natural_function
    def workflow() -> None:
        """natural
        <DomainError>
        {"step_outcome": {"kind": "raise", "raise_message": "domain", "raise_error_type": "DomainError"}, "bindings": {}}
        """

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(events.append)), pytest.raises(DomainError) as caught:
        workflow()
    assert len(events) == 1
    assert isinstance(events[0], StepRaised) and events[0].exception is caught.value


def test_nested_calls_and_scope_configuration_restore() -> None:
    events: list[StepFinished] = []
    other_events: list[StepFinished] = []

    @nh.natural_function
    def inner() -> None:
        """natural
        {"step_outcome": {"kind": "pass"}, "bindings": {}}
        """

    @nh.natural_function
    def outer() -> None:
        """natural
        <inner>
        {"step_outcome": {"kind": "return", "return_expression": "inner()"}, "bindings": {}}
        """

    configuration = StepLifecycle(events.append)
    with nh.run(StubExecutor()), nh.scope(lifecycle=configuration):
        with nh.scope():
            assert nh.get_lifecycle() is configuration
            outer()
        with nh.scope(lifecycle=None):
            inner()
        with nh.scope(lifecycle=StepLifecycle(other_events.append)):
            inner()
        with nh.run(StubExecutor()):
            assert nh.get_lifecycle() is None
            inner()
        assert nh.get_lifecycle() is configuration
    assert len(events) == 2 and len(other_events) == 1
    assert events[0].execution_reference != events[1].execution_reference


def test_partial_assignment_failure_reports_only_completed_writes() -> None:
    events: list[StepFinished] = []

    class Namespace(dict[str, object]):
        def __setitem__(self, name: str, value: object) -> None:
            if name == "second":
                raise ValueError("assignment failed")
            super().__setitem__(name, value)

    class Proxy:
        def execution(self):
            frame = inspect.currentframe()
            assert frame is not None and frame.f_back is not None
            return Runner(nh.get_step_executor()).execution(caller_frame=frame.f_back)

    statements = build_runtime_call_and_assignments(
        ast.Constant("Assign."), (), ("first", "second"), ast.Dict(keys=[], values=[]), ast.Constant(None), is_in_loop=False, is_async_function=False
    )
    # The private module prototype needs no function-level control dispatch.
    module = ast.fix_missing_locations(ast.Module(body=statements[:1], type_ignores=[]))
    namespace = Namespace(__nighthawk_runner__=Proxy())
    with (
        nh.run(ScriptedExecutor([StepResponse(bindings={"first": 1, "second": 2})])),
        nh.scope(lifecycle=StepLifecycle(events.append)),
        pytest.raises(nh.ExecutionError, match="assignment failed"),
    ):
        exec(compile(module, "<assignment-prototype>", "exec"), namespace)
    assert namespace["first"] == 1 and "second" not in namespace
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StepFailed) and event.failure_stage == "binding_assignment"
    assert event.assigned_binding_name_to_value == {"first": 1}


@pytest.mark.parametrize("exception", [KeyboardInterrupt(), SystemExit(3), GeneratorExit()])
def test_process_control_is_interruption(exception: BaseException) -> None:
    events: list[StepFinished] = []

    class Executor:
        def run_step(
            self, *, processed_natural_program: str, step_context: nh.StepContext, binding_names: list[str], allowed_step_kinds: tuple[StepKind, ...]
        ) -> tuple[StepOutcome, dict[str, object]]:
            raise exception

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(events.append)), pytest.raises(type(exception)) as caught:
        workflow()
    assert caught.value is exception
    assert len(events) == 1 and isinstance(events[0], StepInterrupted)


def test_denied_return_never_calls_inspector_or_evaluator() -> None:
    calls: list[str] = []

    def inspect_expression(request: nh.oversight.ReturnExpression) -> nh.oversight.Accept:
        calls.append("inspected")
        return nh.oversight.Accept()

    @nh.natural_function
    def workflow() -> int:
        """natural
        ---
        deny: [return]
        ---
        <calls>
        ignored
        """
        return 0

    with (
        nh.run(ScriptedExecutor([return_response("calls.append('evaluated')")])),
        nh.scope(oversight=nh.oversight.Oversight(inspect_return_expression=inspect_expression)),
        pytest.raises(nh.ExecutionError),
    ):
        workflow()
    assert calls == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_pre_await_failure_preserves_original_exception(asynchronous: bool) -> None:
    failure = ValueError("evaluation failed")
    events: list[StepFinished] = []

    def calculate() -> int:
        raise failure

    @nh.natural_function
    def synchronous() -> int:
        """natural
        <calculate>
        {"step_outcome": {"kind": "return", "return_expression": "calculate()"}, "bindings": {}}
        """
        return 0

    @nh.natural_function
    async def asynchronous_function() -> int:
        """natural
        <calculate>
        {"step_outcome": {"kind": "return", "return_expression": "calculate()"}, "bindings": {}}
        """
        return 0

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(events.append)), pytest.raises(nh.ExecutionError) as caught:
        asyncio.run(asynchronous_function()) if asynchronous else synchronous()
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StepFailed) and event.failure_stage == "return_evaluation"
    assert event.original_exception is failure is caught.value.__cause__


def test_worker_bridge_retains_identity_and_caller_thread_delivery() -> None:
    import threading

    caller_thread = threading.get_ident()
    events: list[StepFinished] = []
    references: list[nh.ExecutionReference] = []

    class Executor:
        async def run_step_async(
            self, *, processed_natural_program: str, step_context: nh.StepContext, binding_names: list[str], allowed_step_kinds: tuple[StepKind, ...]
        ) -> tuple[StepOutcome, dict[str, object]]:
            assert threading.get_ident() != caller_thread
            references.append(nh.get_execution_reference())
            assert references[-1] == step_context.execution_reference
            return PassStepOutcome(kind="pass"), {}

    def finished(event: StepFinished) -> None:
        assert threading.get_ident() == caller_thread
        events.append(event)

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    async def execute() -> None:
        workflow()

    with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(finished)):
        asyncio.run(execute())
    assert [event.execution_reference for event in events] == references


@pytest.mark.parametrize("subject", ["return_expression", "step_commit"])
def test_invalid_inspection_decision_keeps_inspection_stage(subject: str) -> None:
    events: list[StepFinished] = []

    @nh.natural_function
    def workflow() -> int:
        """natural
        {"step_outcome": {"kind": "return", "return_expression": "4"}, "bindings": {}}
        """
        return 0

    oversight = nh.oversight.Oversight(
        inspect_return_expression=(lambda request: None) if subject == "return_expression" else None,  # type: ignore[arg-type]
        inspect_step_commit=(lambda commit: None) if subject == "step_commit" else None,  # type: ignore[arg-type]
    )
    with nh.run(StubExecutor()), nh.scope(oversight=oversight, lifecycle=StepLifecycle(events.append)), pytest.raises(nh.ExecutionError):
        workflow()
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StepFailed)
    assert event.failure_stage == ("return_inspection" if subject == "return_expression" else "commit_inspection")
    assert event.inspection_subject is None


def test_distinct_blocks_have_distinct_source_locations() -> None:
    events: list[StepFinished] = []

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish first.
        """
        """natural
        Finish second.
        """

    with nh.run(ScriptedExecutor()), nh.scope(lifecycle=StepLifecycle(events.append)):
        workflow()
    assert len(events) == 2
    assert events[0].execution_reference.source_location != events[1].execution_reference.source_location


def test_lifecycle_is_captured_before_executor_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from nighthawk.runtime import runner

    events: list[StepFinished] = []
    redirected: list[StepFinished] = []

    class Executor:
        def run_step(
            self, *, processed_natural_program: str, step_context: nh.StepContext, binding_names: list[str], allowed_step_kinds: tuple[StepKind, ...]
        ) -> tuple[StepOutcome, dict[str, object]]:
            # Simulate a later configuration change without changing invocation identity.
            monkeypatch.setattr(runner, "get_lifecycle", lambda: StepLifecycle(redirected.append))
            return PassStepOutcome(kind="pass"), {}

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(events.append)):
        workflow()
    assert len(events) == 1 and not redirected


@pytest.mark.parametrize("interruption", [asyncio.CancelledError(), GeneratorExit()])
def test_handler_rethrowing_interruption_preserves_its_cause(interruption: BaseException) -> None:
    original_cause = ValueError("original cause")
    interruption.__cause__ = original_cause
    notifications: list[StepFinished] = []

    class Executor:
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: nh.StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[StepOutcome, dict[str, object]]:
            raise interruption

    def finished(event: StepFinished) -> None:
        notifications.append(event)
        assert isinstance(event, StepInterrupted)
        raise event.original_exception

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(finished)), pytest.raises(type(interruption)) as caught:
        workflow()
    assert caught.value is interruption
    assert caught.value.__cause__ is original_cause
    assert len(notifications) == 1


def test_handler_rethrowing_prepared_domain_exception_has_no_self_cause() -> None:
    notifications: list[StepFinished] = []

    def finished(event: StepFinished) -> None:
        notifications.append(event)
        assert isinstance(event, StepRaised)
        raise event.exception

    @nh.natural_function
    def workflow() -> None:
        """natural
        <ValueError>
        {"step_outcome": {"kind": "raise", "raise_message": "domain", "raise_error_type": "ValueError"}, "bindings": {}}
        """

    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(finished)), pytest.raises(ValueError) as caught:
        workflow()
    assert caught.value.__cause__ is None
    assert len(notifications) == 1
    event = notifications[0]
    assert isinstance(event, StepRaised) and event.exception is caught.value


def test_admission_can_detect_a_cleared_lifecycle_without_relying_on_delivery() -> None:
    events: list[StepFinished] = []
    bypass_references: list[nh.ExecutionReference] = []
    configuration = StepLifecycle(events.append)

    class Executor:
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: nh.StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[StepOutcome, dict[str, object]]:
            if nh.get_lifecycle() is not configuration:
                # The host boundary must record bypass itself: delivery has been cleared.
                bypass_references.append(step_context.execution_reference)
                raise RuntimeError("mediation bypass")
            return PassStepOutcome(kind="pass"), {}

    @nh.natural_function
    def workflow() -> None:
        """natural
        Finish.
        """

    with nh.run(Executor()), nh.scope(lifecycle=configuration):
        with nh.scope(lifecycle=None), pytest.raises(nh.ExecutionError, match="mediation bypass") as caught:
            workflow()
        assert caught.value.step_failed is not None
        assert caught.value.step_failed.execution_reference == bypass_references[0]
        assert events == []
        assert nh.get_lifecycle() is configuration
        workflow()
    assert len(bypass_references) == len(events) == 1
    assert events[0].execution_reference != bypass_references[0]
    with pytest.raises(nh.NighthawkError):
        nh.get_lifecycle()
