"""Capability requests are typed, mediated, and recorded; perimeter effects are audited first."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable

import pytest

import nighthawk as nh
from nighthawk.agent import (
    Capability,
    CapabilityInvocationEvent,
    CapabilityRejectedError,
    EffectFootprint,
    Grant,
    PerimeterEffectAttemptEvent,
    PerimeterEffectCompletionEvent,
    PerimeterEffectRejectedError,
    RunHandle,
    current_capabilities,
)
from nighthawk.agent.runtime import InMemoryPerimeterAuditStore, Runner
from nighthawk.runtime.step_request import StepCandidate, StepRequest
from nighthawk.testing import CallbackExecutor

from .conftest import FailingAuditStore, make_runner
from .support import EchoInput, EchoResult, run_body


@nh.function
def _act(text: str) -> str:
    outcome: str = ""
    """execute
    Read <text> and set <:outcome>.
    """
    return outcome


def _add(a: int, b: int) -> int:
    return a + b


def _wrong_result(a: int) -> int:
    return "not an int"  # type: ignore[return-value]


def _explode(a: int) -> int:
    raise ZeroDivisionError("division by zero")


def _run_with_handler(handler: Callable[[StepRequest], StepCandidate], grant: Grant, *, runner: Runner | None = None) -> RunHandle:
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=_act(run_input.text))

    run_body(
        body,
        runner=runner if runner is not None else make_runner(),
        step_executor=CallbackExecutor(handler),
        run_entry_grant=grant,
        handle=handle,
    )
    return handle


def test_request_arguments_are_validated_and_coerced_against_the_declared_type() -> None:
    add = Capability(name="add", function=_add, authority_boundary="arithmetic")
    outcomes: list[object] = []

    def handler(call: StepRequest) -> StepCandidate:
        wrapper = current_capabilities()["add"]
        outcomes.append(wrapper("1", 2))  # type: ignore[operator]
        with pytest.raises(CapabilityRejectedError) as captured:
            wrapper("x", 2)  # type: ignore[operator]
        outcomes.append(captured.value.reason_code)
        with pytest.raises(CapabilityRejectedError) as too_many:
            wrapper(1, 2, 3)  # type: ignore[operator]
        outcomes.append(too_many.value.reason_code)
        return StepCandidate(binding_name_to_value={"outcome": "done"})

    handle = _run_with_handler(handler, Grant.of(add))
    assert outcomes == [3, "request_type_invalid", "request_type_invalid"]
    statuses = [entry.event.status for entry in handle.ledger.select(CapabilityInvocationEvent)]  # type: ignore[union-attr]
    assert statuses == ["success", "rejected", "rejected"]


def test_result_outside_the_declared_type_is_rejected() -> None:
    wrong = Capability(name="wrong", function=_wrong_result, authority_boundary="x")
    codes: list[str] = []

    def handler(call: StepRequest) -> StepCandidate:
        with pytest.raises(CapabilityRejectedError) as captured:
            current_capabilities()["wrong"](1)  # type: ignore[operator]
        codes.append(captured.value.reason_code)
        return StepCandidate(binding_name_to_value={"outcome": "done"})

    _run_with_handler(handler, Grant.of(wrong))
    assert codes == ["result_type_invalid"]


def test_capability_exceptions_propagate_unchanged_and_are_recorded() -> None:
    explode = Capability(name="explode", function=_explode, authority_boundary="x")

    def handler(call: StepRequest) -> StepCandidate:
        with pytest.raises(ZeroDivisionError):
            current_capabilities()["explode"](1)  # type: ignore[operator]
        return StepCandidate(binding_name_to_value={"outcome": "done"})

    handle = _run_with_handler(handler, Grant.of(explode))
    invocation = handle.ledger.select(CapabilityInvocationEvent)[0].event
    assert isinstance(invocation, CapabilityInvocationEvent)
    assert invocation.status == "failure"
    assert invocation.failure_class_name == "ZeroDivisionError"


def test_perimeter_effect_is_recorded_durably_before_the_effect_and_completed_after() -> None:
    store = InMemoryPerimeterAuditStore()
    observed_record_count: list[int] = []

    def send(payload: str) -> str:
        observed_record_count.append(len(store.records))
        return f"sent {payload}"

    send_capability = Capability(
        name="send",
        function=send,
        authority_boundary="egress to the test sink",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress"})),
    )

    def handler(call: StepRequest) -> StepCandidate:
        return StepCandidate(binding_name_to_value={"outcome": current_capabilities()["send"]("x")})  # type: ignore[operator]

    handle = _run_with_handler(handler, Grant.of(send_capability), runner=make_runner(audit_store=store))
    assert observed_record_count == [1]
    assert [type(record.event).__name__ for record in store.records] == ["PerimeterEffectAttemptEvent", "PerimeterEffectCompletionEvent"]
    attempt_entry = handle.ledger.select(PerimeterEffectAttemptEvent)[0]
    completion_entry = handle.ledger.select(PerimeterEffectCompletionEvent)[0]
    assert store.records[0].event_id == attempt_entry.event_id
    assert store.records[1].event_id == completion_entry.event_id
    assert completion_entry.parent_event_id == attempt_entry.event_id
    assert isinstance(attempt_entry.event, PerimeterEffectAttemptEvent)
    assert attempt_entry.event.perimeter_effect_class_tuple == ("egress",)
    assert isinstance(completion_entry.event, PerimeterEffectCompletionEvent)
    assert completion_entry.event.status == "success"
    invocation_entry = handle.ledger.select(CapabilityInvocationEvent)[0]
    assert invocation_entry.parent_event_id == completion_entry.event_id


def test_perimeter_effect_is_not_attempted_when_the_attempt_record_fails() -> None:
    attempted: list[str] = []

    def send(payload: str) -> str:
        attempted.append(payload)
        return payload

    send_capability = Capability(
        name="send",
        function=send,
        authority_boundary="egress",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress"})),
    )
    reasons: list[str] = []

    def handler(call: StepRequest) -> StepCandidate:
        with pytest.raises(PerimeterEffectRejectedError) as captured:
            current_capabilities()["send"]("x")  # type: ignore[operator]
        reasons.append(captured.value.reason)
        return StepCandidate(binding_name_to_value={"outcome": "done"})

    handle = _run_with_handler(handler, Grant.of(send_capability), runner=make_runner(audit_store=FailingAuditStore()))
    assert attempted == []
    assert reasons == ["attempt_record_failed"]
    assert handle.ledger.select(PerimeterEffectAttemptEvent) == ()
    rejected = handle.ledger.select(CapabilityInvocationEvent)[0].event
    assert isinstance(rejected, CapabilityInvocationEvent)
    assert rejected.reason_code == "attempt_record_failed"


def test_failed_perimeter_effect_records_a_failure_completion() -> None:
    store = InMemoryPerimeterAuditStore()

    def send(payload: str) -> str:
        raise ConnectionError("unreachable")

    send_capability = Capability(
        name="send",
        function=send,
        authority_boundary="egress",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress"})),
    )

    def handler(call: StepRequest) -> StepCandidate:
        with pytest.raises(ConnectionError):
            current_capabilities()["send"]("x")  # type: ignore[operator]
        return StepCandidate(binding_name_to_value={"outcome": "done"})

    handle = _run_with_handler(handler, Grant.of(send_capability), runner=make_runner(audit_store=store))
    completion = handle.ledger.select(PerimeterEffectCompletionEvent)[0].event
    assert isinstance(completion, PerimeterEffectCompletionEvent)
    assert completion.status == "failure"
    assert completion.failure_class_name == "ConnectionError"
    assert len(store.records) == 2


async def _fetch(key: str) -> str:
    return f"fetched {key}"


def test_async_capabilities_are_mediated_as_coroutine_functions() -> None:
    fetch = Capability(name="fetch", function=_fetch, authority_boundary="async lookup")

    def body(run_input: EchoInput) -> EchoResult:
        wrapper = current_capabilities()["fetch"]
        assert inspect.iscoroutinefunction(wrapper)
        return EchoResult(value=asyncio.run(wrapper("k")))  # type: ignore[arg-type]

    handle = RunHandle()
    result = run_body(body, runner=make_runner(), run_entry_grant=Grant.of(fetch), handle=handle)
    assert result.value == "fetched k"
    assert handle.ledger.select(CapabilityInvocationEvent)[0].event.status == "success"  # type: ignore[union-attr]


def test_wrapper_is_rejected_after_the_run_ended() -> None:
    add = Capability(name="add", function=_add, authority_boundary="arithmetic")
    retained: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        retained.append(current_capabilities()["add"])
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(add))
    with pytest.raises(CapabilityRejectedError) as captured:
        retained[0](1, 2)  # type: ignore[operator]
    assert captured.value.reason_code == "run_ended"


def _stringy(a: int) -> int:
    return "7"  # type: ignore[return-value]


def test_capability_results_are_committed_as_their_validated_values() -> None:
    stringy = Capability(name="stringy", function=_stringy, authority_boundary="x")
    outcomes: list[object] = []

    def handler(call: StepRequest) -> StepCandidate:
        outcomes.append(current_capabilities()["stringy"](1))  # type: ignore[operator]
        return StepCandidate(binding_name_to_value={"outcome": "done"})

    handle = _run_with_handler(handler, Grant.of(stringy))
    assert outcomes == [7]
    assert isinstance(outcomes[0], int)
    invocation = handle.ledger.select(CapabilityInvocationEvent)[0].event
    assert isinstance(invocation, CapabilityInvocationEvent)
    assert invocation.result == 7
    assert invocation.result_type_name == "builtins.int"


@pytest.mark.parametrize("outcome", ["success", "failure", "invalid_result"])
def test_invocation_evidence_preserves_inputs_before_body_mutation(outcome: str) -> None:
    retained = [1]
    handle = RunHandle()

    def mutate(values: object, increment: int) -> int:
        assert values is retained
        retained.append(increment)
        if outcome == "failure":
            raise ValueError("body failed after mutation")
        return "invalid" if outcome == "invalid_result" else increment  # type: ignore[return-value]

    capability = Capability(name="mutate", function=mutate, authority_boundary="change the supplied list")

    def body(value: EchoInput) -> EchoResult:
        function = current_capabilities()["mutate"]
        assert callable(function)
        if outcome == "success":
            assert function(retained, "2") == 2
        else:
            with pytest.raises(CapabilityRejectedError if outcome == "invalid_result" else ValueError):
                function(retained, "2")
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(capability), handle=handle)
    retained.append(3)
    event = handle.ledger.select(CapabilityInvocationEvent)[0].event
    assert isinstance(event, CapabilityInvocationEvent)
    assert event.argument_snapshot == {"positional": ((1,), 2), "keyword": {}}
    assert event.status == {"success": "success", "failure": "failure", "invalid_result": "rejected"}[outcome]
    assert retained == [1, 2, 3]


async def test_async_cancellation_retains_admitted_input_evidence() -> None:
    from nighthawk.agent.runtime import AsyncScaffoldComposition, RunContract

    entered = asyncio.Event()
    retained = [1]
    handle = RunHandle()

    async def wait(values: object) -> None:
        retained.append(2)
        entered.set()
        await asyncio.Event().wait()

    capability = Capability(name="wait", function=wait, authority_boundary="wait for cancellation")

    async def body(value: str) -> str:
        function = current_capabilities()["wait"]
        assert callable(function)
        pending = function(retained)
        assert asyncio.iscoroutine(pending)
        task = asyncio.create_task(pending)
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return value

    await make_runner().run(AsyncScaffoldComposition(RunContract(str, str), body, run_entry_grant=Grant.of(capability)), "done", handle=handle)
    event = handle.ledger.select(CapabilityInvocationEvent)[0].event
    assert isinstance(event, CapabilityInvocationEvent)
    assert event.argument_snapshot == {"positional": ((1,),), "keyword": {}}
    assert event.status == "failure" and event.failure_class_name == "CancelledError"
