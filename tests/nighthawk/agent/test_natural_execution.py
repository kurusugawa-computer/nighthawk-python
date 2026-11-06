"""Every Natural execution passes through host mediation and commits one outcome event."""

from __future__ import annotations

import contextvars
import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast

import pytest
from pydantic import AfterValidator, BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.agent import (
    Capability,
    CapabilityRejectedError,
    ExecutorUsage,
    Grant,
    GrantError,
    NaturalExecutionError,
    NaturalExecutionOutcomeEvent,
    ResourceExhaustionEvent,
    RunHandle,
    TraceAnomalyEvent,
    scope,
)
from nighthawk.agent.runtime import ResourceConsumption, ResourceRejection, ResourceRequest, Runner, bounded_resource_allocation
from nighthawk.runtime.step_executor import StepExecutor
from nighthawk.runtime.step_outcome import Pass, Raise, Return
from nighthawk.runtime.step_request import StepCandidate, StepRequest
from nighthawk.testing import CallbackExecutor, ScriptedExecutor

from .conftest import make_runner
from .support import EchoInput, EchoResult, run_body


@nh.function
def _count(text: str) -> int:
    count: int = 0
    """execute
    Set <:count> from <text>.
    """
    return count


@nh.function
def _no_return(text: str) -> str:
    """execute
    ---
    deny: [return]
    ---
    Read <text>.
    """
    return "fallthrough"


def _lookup(key: str) -> str:
    """Look up a key."""

    return f"value:{key}"


def _run_count(runner: Runner, executor: StepExecutor, **run_body_arguments: object) -> tuple[EchoResult, RunHandle]:
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        try:
            return EchoResult(value=str(_count(run_input.text)))
        except NaturalExecutionError as failure:
            return EchoResult(value=f"{failure.failure_kind}:{failure.outcome_event_id}")

    result = run_body(body, runner=runner, step_executor=executor, handle=handle, **run_body_arguments)  # type: ignore[arg-type]
    return result, handle


def test_success_commits_the_validated_write_set_and_records_one_outcome(runner: Runner) -> None:
    result, handle = _run_count(runner, ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": "3"})]))
    assert result == EchoResult(value="3")
    outcomes = handle.ledger.select(NaturalExecutionOutcomeEvent)
    assert len(outcomes) == 1
    outcome = outcomes[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.status == "success"
    assert outcome.outcome_kind == "pass"
    assert outcome.committed_binding_name_tuple == ("count",)
    assert outcome.executor_step_count == 1
    assert outcome.natural_block_identity.startswith(__name__)
    assert outcomes[0].parent_event_id == handle.ledger.entries[0].event_id


def test_invalid_write_set_fails_the_execution_after_recording_the_failed_outcome(runner: Runner) -> None:
    result, handle = _run_count(runner, ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": "not a number"})]))
    outcome_entry = handle.ledger.select(NaturalExecutionOutcomeEvent)[0]
    assert result.value == f"write_set_invalid:{outcome_entry.event_id}"
    assert isinstance(outcome_entry.event, NaturalExecutionOutcomeEvent)
    assert outcome_entry.event.status == "failure"
    assert outcome_entry.event.failure_kind == "write_set_invalid"
    assert outcome_entry.event.committed_binding_name_tuple == ()


def test_raise_outcome_surfaces_as_the_library_exception_with_the_requested_error_type(runner: Runner) -> None:
    handle = RunHandle()
    captured: list[NaturalExecutionError] = []

    def body(run_input: EchoInput) -> EchoResult:
        try:
            _count(run_input.text)
        except NaturalExecutionError as failure:
            captured.append(failure)
        return EchoResult()

    run_body(
        body,
        runner=runner,
        step_executor=ScriptedExecutor(responses=[StepCandidate(outcome=Raise("bad input", error_type=ValueError))]),
        handle=handle,
    )
    assert len(captured) == 1
    assert captured[0].failure_kind == "raise_outcome"
    assert captured[0].raise_error_type_name == "ValueError"
    assert "bad input" in str(captured[0])
    assert handle.ledger.find(captured[0].outcome_event_id) is not None


def test_denied_outcome_kind_is_a_failed_execution(runner: Runner) -> None:
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        try:
            _no_return(run_input.text)
        except NaturalExecutionError as failure:
            return EchoResult(value=failure.failure_kind)
        return EchoResult(value="allowed")

    result = run_body(body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(outcome=Return("x"))]), handle=handle)
    assert result.value == "disallowed_outcome_kind"


def test_executor_error_is_wrapped_and_chained(runner: Runner) -> None:
    def handler(call: StepRequest) -> StepCandidate:
        raise RuntimeError("provider unavailable")

    handle = RunHandle()
    captured: list[NaturalExecutionError] = []

    def body(run_input: EchoInput) -> EchoResult:
        try:
            _count(run_input.text)
        except NaturalExecutionError as failure:
            captured.append(failure)
        return EchoResult()

    run_body(body, runner=runner, step_executor=CallbackExecutor(handler), handle=handle)
    assert captured[0].failure_kind == "executor_error"
    assert isinstance(captured[0].__cause__, RuntimeError)


def test_implicit_references_outside_the_grant_are_a_trace_anomaly_and_a_grant_violation(runner: Runner) -> None:
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        with nh.scope(task_references={"rogue": _lookup}):
            try:
                _count(run_input.text)
            except NaturalExecutionError as failure:
                return EchoResult(value=failure.failure_kind)
        return EchoResult(value="leaked")

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]), handle=handle
    )
    assert result.value == "grant_violation"
    anomaly = handle.ledger.select(TraceAnomalyEvent)[0].event
    assert isinstance(anomaly, TraceAnomalyEvent)
    assert anomaly.anomaly_kind == "grant_projection_mismatch"
    assert "rogue" in anomaly.detail


def test_projection_is_exactly_the_grant_capabilities_with_name_signature_and_authority() -> None:
    seen: list[dict[str, object]] = []

    def handler(call: StepRequest) -> StepCandidate:
        seen.append(dict(nh.get_step_context().environment.task_name_to_value))
        return StepCandidate(binding_name_to_value={"count": 2})

    lookup = Capability(name="lookup", function=_lookup, authority_boundary="read-only lookups")
    result, _handle = _run_count(make_runner(), CallbackExecutor(handler), run_entry_grant=Grant.of(lookup, hidden_reference=42))
    assert result.value == "2"
    assert set(seen[0]) == {"lookup"}
    wrapper = cast(Callable[..., object], seen[0]["lookup"])
    assert wrapper.__name__ == "lookup"
    assert "[authority: read-only lookups]" in (wrapper.__doc__ or "")
    assert "Look up a key." in (wrapper.__doc__ or "")
    assert str(inspect.signature(wrapper)) == "(key: str) -> str"
    assert not hasattr(wrapper, "__wrapped__")


def test_scope_attenuates_visibility_and_rejects_widening(runner: Runner) -> None:
    seen: list[set[str]] = []

    def handler(call: StepRequest) -> StepCandidate:
        seen.append(set(nh.get_step_context().environment.task_name_to_value))
        return StepCandidate(binding_name_to_value={"count": len(seen)})

    lookup = Capability(name="lookup", function=_lookup, authority_boundary="a")
    other = Capability(name="other", function=_lookup, authority_boundary="b")
    grant = Grant.of(lookup, other)

    def body(run_input: EchoInput) -> EchoResult:
        _count(run_input.text)
        with scope(grant.without(["other"])):
            _count(run_input.text)
            with scope(Grant.EMPTY):
                _count(run_input.text)
            with pytest.raises(GrantError) as captured, scope(grant):
                pass
            assert captured.value.reason == "authority_widening"
            assert captured.value.name_tuple == ("other",)
        _count(run_input.text)
        return EchoResult()

    run_body(body, runner=runner, step_executor=CallbackExecutor(handler), run_entry_grant=grant)
    assert seen == [{"lookup", "other"}, {"lookup"}, set(), {"lookup", "other"}]


def test_retained_wrapper_is_rejected_outside_a_scope_that_grants_it(runner: Runner) -> None:
    retained: list[object] = []

    def handler(call: StepRequest) -> StepCandidate:
        retained.append(nh.get_step_context().environment.task_name_to_value["lookup"])
        return StepCandidate(binding_name_to_value={"count": 1})

    lookup = Capability(name="lookup", function=_lookup, authority_boundary="a")

    def body(run_input: EchoInput) -> EchoResult:
        _count(run_input.text)
        wrapper = retained[0]
        assert wrapper("k") == "value:k"  # type: ignore[operator]
        with scope(Grant.EMPTY), pytest.raises(CapabilityRejectedError) as captured:
            wrapper("k")  # type: ignore[operator]
        return EchoResult(value=captured.value.reason_code)

    result = run_body(body, runner=runner, step_executor=CallbackExecutor(handler), run_entry_grant=Grant.of(lookup))
    assert result.value == "not_in_current_grant"


def test_allocation_exhaustion_is_a_typed_recorded_failure_that_python_may_handle() -> None:
    runner = make_runner(allocation_factory=lambda run_identity: bounded_resource_allocation(maximum_natural_execution_count=1))
    handle = RunHandle()
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1}), StepCandidate(binding_name_to_value={"count": 2})])

    def body(run_input: EchoInput) -> EchoResult:
        first = _count(run_input.text)
        try:
            _count(run_input.text)
        except NaturalExecutionError as failure:
            return EchoResult(value=f"{first}:{failure.failure_kind}")
        return EchoResult(value="not exhausted")

    result = run_body(body, runner=runner, step_executor=executor, handle=handle)
    assert result.value == "1:allocation_exhausted"
    exhaustion = handle.ledger.select(ResourceExhaustionEvent)[0]
    assert isinstance(exhaustion.event, ResourceExhaustionEvent)
    assert exhaustion.event.request_kind == "natural_execution"
    failure_entry = handle.ledger.select(NaturalExecutionOutcomeEvent)[1]
    assert failure_entry.parent_event_id == exhaustion.event_id
    assert len(executor.requests) == 1


class _RecordingAllocation:
    identity = "recording"

    def __init__(self) -> None:
        self.consumption_list: list[ResourceConsumption] = []

    def admit(self, request: ResourceRequest) -> ResourceRejection | None:
        return None

    def record(self, consumption: ResourceConsumption) -> None:
        self.consumption_list.append(consumption)


class _MeteringExecutor:
    """Executor double that reports usage like a provider-backed executor."""

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        meter = nh.get_scope().usage_meter
        assert meter is not None
        meter.record(RunUsage(requests=3, input_tokens=40, output_tokens=2))
        return StepCandidate(outcome=Pass(), binding_name_to_value={"count": 7})


def test_executor_usage_is_metered_per_natural_execution_and_reported_to_the_platform() -> None:
    allocation = _RecordingAllocation()
    runner = make_runner(allocation_factory=lambda run_identity: allocation)
    result, handle = _run_count(runner, _MeteringExecutor())
    assert result.value == "7"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.executor_step_count == 3
    assert outcome.executor_usage.input_token_count == 40
    assert outcome.executor_usage.total_token_count == 42
    assert [consumption.executor_step_count for consumption in allocation.consumption_list] == [3]
    assert allocation.consumption_list[0].attribution_node_id == outcome.attribution_node_id


class _AsyncOnlyExecutor:
    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        return StepCandidate(outcome=Pass(), binding_name_to_value={"count": 5})


def test_async_only_delegate_executor_is_mediated(runner: Runner) -> None:
    result, handle = _run_count(runner, _AsyncOnlyExecutor())
    assert result.value == "5"
    assert len(handle.ledger.select(NaturalExecutionOutcomeEvent)) == 1


@nh.function
async def _count_async(text: str) -> int:
    count: int = 0
    """execute
    Set <:count> from <text>.
    """
    return count


def test_async_natural_function_is_mediated(runner: Runner) -> None:
    import asyncio

    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=str(asyncio.run(_count_async(run_input.text))))

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 9})]), handle=handle
    )
    assert result.value == "9"
    assert len(handle.ledger.select(NaturalExecutionOutcomeEvent)) == 1


@nh.function
def _judge_count(text: str) -> int:
    count: int = 0
    """execute
    Set <:count> from <text>.
    """
    return count


def test_return_outcome_is_not_offered_inside_an_agent_run_and_fails_as_a_typed_outcome(runner: Runner) -> None:
    executor = ScriptedExecutor(responses=[StepCandidate(outcome=Return("text"))])
    handle = RunHandle()
    captured: list[NaturalExecutionError] = []

    def body(run_input: EchoInput) -> EchoResult:
        try:
            _judge_count(run_input.text)
        except NaturalExecutionError as failure:
            captured.append(failure)
        return EchoResult()

    run_body(body, runner=runner, step_executor=executor, handle=handle)
    assert "return" not in executor.requests[0].allowed_outcome_kinds
    assert "pass" in executor.requests[0].allowed_outcome_kinds
    assert len(captured) == 1
    assert captured[0].failure_kind == "disallowed_outcome_kind"
    outcomes = handle.ledger.select(NaturalExecutionOutcomeEvent)
    assert len(outcomes) == 1
    assert isinstance(outcomes[0].event, NaturalExecutionOutcomeEvent)
    assert outcomes[0].event.status == "failure"
    assert outcomes[0].event_id == captured[0].outcome_event_id


def test_success_outcome_records_the_committed_values(runner: Runner) -> None:
    result, handle = _run_count(runner, ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": "41"})]))
    assert result == EchoResult(value="41")
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.committed_binding_name_to_value == {"count": 41}
    assert isinstance(outcome.committed_binding_name_to_value["count"], int)


def test_core_oversight_hook_around_a_natural_execution_is_a_mediation_bypass(runner: Runner) -> None:
    from nighthawk.oversight import Accept, Oversight

    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        with nh.scope(oversight=Oversight(inspect_step_commit=lambda proposal: Accept())):
            try:
                _count(run_input.text)
            except NaturalExecutionError as failure:
                return EchoResult(value=failure.failure_kind)
        return EchoResult(value="inspected")

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]), handle=handle
    )
    assert result.value == "mediation_bypass"
    anomaly = handle.ledger.select(TraceAnomalyEvent)[0].event
    assert isinstance(anomaly, TraceAnomalyEvent)
    assert anomaly.anomaly_kind == "oversight_hook_active"


def test_clearing_the_host_commit_inspector_is_a_mediation_bypass(runner: Runner) -> None:
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        with nh.scope(oversight=None):
            try:
                _count(run_input.text)
            except NaturalExecutionError as failure:
                return EchoResult(value=failure.failure_kind)
        return EchoResult(value="committed without the host")

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]), handle=handle
    )
    assert result.value == "mediation_bypass"
    anomaly = handle.ledger.select(TraceAnomalyEvent)[0].event
    assert isinstance(anomaly, TraceAnomalyEvent)
    assert anomaly.anomaly_kind == "oversight_hook_active"
    assert "cleared" in anomaly.detail


def test_clearing_the_host_lifecycle_is_a_mediation_bypass(runner: Runner) -> None:
    """Without the host lifecycle the core wraps the host rejection, so the run fails with that wrapped exception."""

    from nighthawk.agent import RunFailedError

    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        with nh.scope(lifecycle=None):
            return EchoResult(value=str(_count(run_input.text)))

    with pytest.raises(RunFailedError) as captured:
        run_body(body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]), handle=handle)
    assert captured.value.terminal_kind == "uncaught_exception"
    wrapped = captured.value.__cause__
    assert isinstance(wrapped, nh.ExecutionError)
    assert isinstance(wrapped.__cause__, NaturalExecutionError)
    assert wrapped.__cause__.failure_kind == "mediation_bypass"
    anomaly = handle.ledger.select(TraceAnomalyEvent)[0].event
    assert isinstance(anomaly, TraceAnomalyEvent)
    assert anomaly.anomaly_kind == "lifecycle_hook_replaced"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.failure_kind == "mediation_bypass"


def test_raw_dynamic_execution_in_natural_expressions_is_rejected_and_recorded(runner: Runner) -> None:
    from nighthawk.oversight import Accept, RejectOperation
    from nighthawk.runtime.operation_contract import OperationRequest

    decisions: list[object] = []

    def handler(call: StepRequest) -> StepCandidate:
        oversight = nh.get_scope().oversight
        assert oversight is not None and oversight.inspect_operation is not None
        for expression in ("len(items)", "items.append(1)", "eval('1 + 1')", "__import__('os').getcwd()", "importlib.import_module('os')"):
            operation = OperationRequest(
                execution_reference=nh.get_scope().execution_reference,
                tool_name="nh_evaluate",
                argument_name_to_value={"expression": expression},
                operation_id=f"test-{len(decisions)}",
                kind="evaluation",
                expression=expression,
            )
            decisions.append(oversight.inspect_operation(operation))
        return StepCandidate(binding_name_to_value={"count": 1})

    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=str(_count(run_input.text)))

    run_body(body, runner=runner, step_executor=CallbackExecutor(handler), handle=handle)
    assert [type(decision).__name__ for decision in decisions] == ["Accept", "Accept", "RejectOperation", "RejectOperation", "RejectOperation"]
    assert all(isinstance(decision, Accept | RejectOperation) for decision in decisions)
    anomalies = [entry.event for entry in handle.ledger.select(TraceAnomalyEvent)]
    assert [anomaly.anomaly_kind for anomaly in anomalies] == ["expression_raw_dynamic_execution"] * 3  # type: ignore[union-attr]
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.status == "success"


class _Interruption(BaseException):
    pass


def test_interrupted_execution_records_an_interrupted_outcome_and_propagates_the_interruption(runner: Runner) -> None:
    def handler(call: StepRequest) -> StepCandidate:
        raise _Interruption()

    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=str(_count(run_input.text)))

    with pytest.raises(_Interruption):
        run_body(body, runner=runner, step_executor=CallbackExecutor(handler), handle=handle)
    outcomes = handle.ledger.select(NaturalExecutionOutcomeEvent)
    assert len(outcomes) == 1
    assert isinstance(outcomes[0].event, NaturalExecutionOutcomeEvent)
    assert outcomes[0].event.failure_kind == "interrupted"


def test_success_is_recorded_at_the_terminal_delivery_after_assignment(runner: Runner) -> None:
    observed: list[tuple[int, object]] = []
    handle = RunHandle()

    class _ObservingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            observed.append((len(handle.ledger.select(NaturalExecutionOutcomeEvent)), "executor"))
            return StepCandidate(outcome=Pass(), binding_name_to_value={"count": "5"})

    def body(run_input: EchoInput) -> EchoResult:
        value = _count(run_input.text)
        observed.append((len(handle.ledger.select(NaturalExecutionOutcomeEvent)), value))
        return EchoResult(value=str(value))

    result = run_body(body, runner=runner, step_executor=_ObservingExecutor(), handle=handle)
    assert result.value == "5"
    assert observed == [(0, "executor"), (1, 5)]
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.status == "success"
    assert outcome.committed_binding_name_to_value == {"count": 5}


def test_core_tools_registered_outside_the_grant_are_a_grant_violation(runner: Runner) -> None:
    handle = RunHandle()

    def rogue_tool(key: str) -> str:
        return key

    def body(run_input: EchoInput) -> EchoResult:
        with nh.scope(tools=[rogue_tool]):
            try:
                _count(run_input.text)
            except NaturalExecutionError as failure:
                return EchoResult(value=failure.failure_kind)
        return EchoResult(value="visible")

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]), handle=handle
    )
    assert result.value == "grant_violation"
    anomaly = handle.ledger.select(TraceAnomalyEvent)[0].event
    assert isinstance(anomaly, TraceAnomalyEvent)
    assert anomaly.anomaly_kind == "ambient_tool_visible"
    assert "rogue_tool" in anomaly.detail


def test_pydantic_ai_capabilities_declared_outside_the_grant_are_a_grant_violation(runner: Runner) -> None:
    from pydantic_ai.capabilities import AbstractCapability

    class _Ambient(AbstractCapability[nh.StepContext]):
        def __init__(self) -> None:
            self.id = "ambient_capability"

    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        with nh.scope(capabilities=[_Ambient()]):
            try:
                _count(run_input.text)
            except NaturalExecutionError as failure:
                return EchoResult(value=failure.failure_kind)
        return EchoResult(value="visible")

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]), handle=handle
    )
    assert result.value == "grant_violation"
    anomaly = handle.ledger.select(TraceAnomalyEvent)[0].event
    assert isinstance(anomaly, TraceAnomalyEvent)
    assert "ambient_capability" in anomaly.detail


def test_aggregate_metered_steps_beyond_the_allocation_fail_the_execution() -> None:
    runner = make_runner(allocation_factory=lambda run_identity: bounded_resource_allocation(maximum_executor_step_count=1))
    result, handle = _run_count(runner, _MeteringExecutor())
    assert result.value.startswith("allocation_exhausted:")
    exhaustion = handle.ledger.select(ResourceExhaustionEvent)[0].event
    assert isinstance(exhaustion, ResourceExhaustionEvent)
    assert exhaustion.request_kind == "executor_step"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.status == "failure"
    assert outcome.failure_kind == "allocation_exhausted"
    assert outcome.executor_step_count == 3


def test_model_requests_of_the_core_agent_executor_are_admitted_per_executor_step() -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from nighthawk.runtime.model_outcome import ModelStepResponse
    from nighthawk.runtime.step_context import StepContext

    agent = Agent(
        model=TestModel(call_tools=["nh_evaluate"], custom_output_args={"result": {"kind": "pass"}}),
        output_type=ModelStepResponse,
        deps_type=StepContext,
    )
    executor = nh.AgentStepExecutor.from_agent(agent=agent)

    result, handle = _run_count(make_runner(), executor)
    assert result.value == "0"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.executor_step_count == 2
    assert outcome.executor_usage.request_count == 2

    bounded = make_runner(allocation_factory=lambda run_identity: bounded_resource_allocation(maximum_executor_step_count=1))
    result, handle = _run_count(bounded, executor)
    assert result.value.startswith("allocation_exhausted:")
    exhaustion = handle.ledger.select(ResourceExhaustionEvent)[0].event
    assert isinstance(exhaustion, ResourceExhaustionEvent)
    assert exhaustion.request_kind == "executor_step"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.status == "failure"
    assert outcome.executor_step_count == 1


class _NestingExecutor:
    """Executor double whose outer execution invokes a nested Natural function and meters its own requests."""

    def __init__(self, inner: Callable[[str], int]) -> None:
        self._inner = inner

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        processed_natural_program = request.processed_natural_program
        meter = nh.get_scope().usage_meter
        assert meter is not None
        if "outer" in processed_natural_program:
            meter.record(RunUsage(requests=1, input_tokens=10))
            nested = self._inner("nested")
            meter.record(RunUsage(requests=2, input_tokens=20))
            return StepCandidate(outcome=Pass(), binding_name_to_value={"count": nested + 1})
        meter.record(RunUsage(requests=2, input_tokens=5))
        return StepCandidate(outcome=Pass(), binding_name_to_value={"count": 7})


@nh.function
def _outer_count(text: str) -> int:
    count: int = 0
    """execute
    outer: set <:count> from <text>.
    """
    return count


def test_nested_natural_execution_usage_is_attributed_once() -> None:
    allocation = _RecordingAllocation()
    runner = make_runner(allocation_factory=lambda run_identity: allocation)
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=str(_outer_count(run_input.text)))

    result = run_body(body, runner=runner, step_executor=_NestingExecutor(_count), handle=handle)
    assert result.value == "8"
    outcomes = [entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent)]
    assert [outcome.executor_usage.request_count for outcome in outcomes] == [2, 3]  # type: ignore[union-attr]
    assert [outcome.executor_usage.input_token_count for outcome in outcomes] == [5, 30]  # type: ignore[union-attr]
    assert [outcome.executor_step_count for outcome in outcomes] == [2, 3]  # type: ignore[union-attr]
    assert handle.ledger.executor_step_count() == 5
    assert sum(consumption.executor_usage.request_count for consumption in allocation.consumption_list) == 5


type _Incremented = Annotated[int, AfterValidator(lambda value: value + 1)]


@nh.function
def _increment(text: str) -> int:
    count: _Incremented = 0
    """execute
    Set <:count> from <text>.
    """
    return count


def test_write_set_is_validated_exactly_once_so_ledger_and_locals_agree(runner: Runner) -> None:
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=str(_increment(run_input.text)))

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]), handle=handle
    )
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.committed_binding_name_to_value["count"] == 2
    assert result.value == "2"


def test_committed_write_set_in_the_ledger_is_read_only(runner: Runner) -> None:
    _result, handle = _run_count(runner, ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]))
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    with pytest.raises(TypeError):
        outcome.committed_binding_name_to_value["count"] = "forged"  # type: ignore[index]
    assert outcome.committed_binding_name_to_value["count"] == 1


@nh.function
def _collect(text: str) -> list[int]:
    items: list[int] = []
    """execute
    Set <:items> from <text>.
    """
    return items


def test_committed_mutable_values_are_snapshots_that_later_mutation_cannot_alter(runner: Runner) -> None:
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        items = _collect(run_input.text)
        items.append(99)
        return EchoResult(value=str(len(items)))

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"items": [1, 2]})]), handle=handle
    )
    assert result.value == "3"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.committed_binding_name_to_value["items"] == (1, 2)


def _core_agent_executor(call_tools: list[str]) -> nh.AgentStepExecutor:
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from nighthawk.runtime.model_outcome import ModelStepResponse
    from nighthawk.runtime.step_context import StepContext

    agent = Agent(
        model=TestModel(call_tools=call_tools, custom_output_args={"result": {"kind": "pass"}}),
        output_type=ModelStepResponse,
        deps_type=StepContext,
    )
    return nh.AgentStepExecutor.from_agent(agent=agent)


def test_observed_model_requests_account_usage_immediately_and_on_failure() -> None:
    executor = _core_agent_executor(["nh_evaluate"])
    bounded_steps = make_runner(allocation_factory=lambda run_identity: bounded_resource_allocation(maximum_executor_step_count=1))
    result, handle = _run_count(bounded_steps, executor)
    assert result.value.startswith("allocation_exhausted:")
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.executor_step_count == 1
    assert outcome.executor_usage.request_count == 1
    assert outcome.executor_usage.total_token_count > 0

    bounded_tokens = make_runner(allocation_factory=lambda run_identity: bounded_resource_allocation(maximum_total_token_count=1))
    result, handle = _run_count(bounded_tokens, executor)
    assert result.value.startswith("allocation_exhausted:")
    exhaustion = handle.ledger.select(ResourceExhaustionEvent)[0].event
    assert isinstance(exhaustion, ResourceExhaustionEvent)
    assert exhaustion.request_kind == "executor_step"
    assert exhaustion.reason == "total_token_count_exhausted"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.executor_step_count == 1


class _ConcurrentMeteringExecutor:
    """Executor double whose executions overlap in time and each meter their own requests."""

    def __init__(self) -> None:
        self.started = threading.Barrier(2)

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        processed_natural_program = request.processed_natural_program
        meter = nh.get_scope().usage_meter
        assert meter is not None
        request_count = 1 if "one" in processed_natural_program else 2
        self.started.wait(timeout=5)
        meter.record(RunUsage(requests=request_count, input_tokens=request_count * 10))
        self.started.wait(timeout=5)
        return StepCandidate(outcome=Pass(), binding_name_to_value={"count": request_count})


@nh.function
def _count_one(text: str) -> int:
    count: int = 0
    """execute
    one: set <:count> from <text>.
    """
    return count


@nh.function
def _count_two(text: str) -> int:
    count: int = 0
    """execute
    two: set <:count> from <text>.
    """
    return count


def test_concurrent_natural_executions_attribute_their_own_usage() -> None:
    allocation = _RecordingAllocation()
    runner = make_runner(allocation_factory=lambda run_identity: allocation)
    handle = RunHandle()
    executor = _ConcurrentMeteringExecutor()

    def body(run_input: EchoInput) -> EchoResult:
        outcomes: list[object] = []
        context = contextvars.copy_context()

        def run_two() -> None:
            outcomes.append(context.run(_count_two, run_input.text))

        thread = threading.Thread(target=run_two, daemon=True)
        thread.start()
        outcomes.append(_count_one(run_input.text))
        thread.join(timeout=5)
        return EchoResult(value=str(sorted(int(outcome) for outcome in outcomes)))  # type: ignore[arg-type]

    result = run_body(body, runner=runner, step_executor=executor, handle=handle)
    assert result.value == "[1, 2]"
    outcomes = [entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent)]
    assert sorted(outcome.executor_usage.request_count for outcome in outcomes) == [1, 2]  # type: ignore[union-attr]
    assert sorted(outcome.executor_usage.input_token_count for outcome in outcomes) == [10, 20]  # type: ignore[union-attr]
    assert handle.ledger.executor_step_count() == 3
    assert sum(consumption.executor_usage.request_count for consumption in allocation.consumption_list) == 3
    run_meter_total = sum(consumption.executor_usage.input_token_count for consumption in allocation.consumption_list)
    assert run_meter_total == 30


class _SelfAdmittingExecutor:
    """Executor double that presents each of its LLM requests to the host itself."""

    admits_executor_steps = True

    def __init__(self, request_count: int) -> None:
        self.request_count = request_count
        self.admitted_before_request: list[bool] = []

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        from nighthawk.agent import current_executor_step_admission

        admission = current_executor_step_admission()
        for _ in range(self.request_count):
            admission.admit()
            self.admitted_before_request.append(True)
            admission.observe(RunUsage(requests=1, input_tokens=5))
        return StepCandidate(outcome=Pass(), binding_name_to_value={"count": self.request_count})


def test_self_admitting_executor_is_observed_per_step_without_aggregate_acceptance() -> None:
    executor = _SelfAdmittingExecutor(request_count=2)
    runner = make_runner(accepts_aggregate_executor_accounting=False)
    result, handle = _run_count(runner, executor)
    assert result.value == "2"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.executor_step_count == 2
    assert outcome.executor_usage == ExecutorUsage(request_count=2, input_token_count=10, total_token_count=10)

    bounded = make_runner(
        accepts_aggregate_executor_accounting=False,
        allocation_factory=lambda run_identity: bounded_resource_allocation(maximum_executor_step_count=1),
    )
    executor = _SelfAdmittingExecutor(request_count=3)
    result, handle = _run_count(bounded, executor)
    assert result.value.startswith("allocation_exhausted:")
    assert executor.admitted_before_request == [True]


def test_unobservable_executor_is_rejected_at_run_entry_unless_the_platform_accepts_aggregate_accounting() -> None:
    from nighthawk.agent import RunRejectedError

    with pytest.raises(RunRejectedError) as captured:
        _run_count(
            make_runner(accepts_aggregate_executor_accounting=False), ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})])
        )
    assert captured.value.reason == "executor_steps_unobservable"
    result, _handle = _run_count(make_runner(accepts_aggregate_executor_accounting=False), _core_agent_executor([]))
    assert result.value == "0"


class _ExtraModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    name: str


@nh.function
def _describe(text: str) -> _ExtraModel:
    described: _ExtraModel = _ExtraModel(name="")
    """execute
    Set <:described> from <text>.
    """
    return described


def test_frozen_records_with_mutable_extra_values_are_snapshotted_as_mappings(runner: Runner) -> None:
    handle = RunHandle()
    described = _ExtraModel(name="a", notes=[])  # type: ignore[call-arg]

    def body(run_input: EchoInput) -> EchoResult:
        result = _describe(run_input.text)
        result.notes.append("later")  # type: ignore[attr-defined]
        return EchoResult(value=result.name)

    run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"described": described})]), handle=handle
    )
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    recorded = outcome.committed_binding_name_to_value["described"]
    assert recorded is not described
    assert recorded["name"] == "a"  # type: ignore[index]
    assert recorded["notes"] == ()  # type: ignore[index]


class _Unsnapshottable:
    def __nighthawk_ledger_snapshot__(self) -> object:
        raise ValueError("no audit representation")


@nh.function
def _hold(text: str) -> object:
    held: object = None
    """execute
    Set <:held> from <text>.
    """
    return held


def test_snapshot_failures_never_prevent_the_outcome_record(runner: Runner) -> None:
    handle = RunHandle()
    value = _Unsnapshottable()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=str(_hold(run_input.text) is value))

    result = run_body(
        body, runner=runner, step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"held": value})]), handle=handle
    )
    assert result.value == "True"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.status == "success"
    assert str(outcome.committed_binding_name_to_value["held"]).startswith("<snapshot failed ")
    assert handle.ledger.entries[-1].event.kind == "run_end"


class _FailingReportAllocation(_RecordingAllocation):
    def record(self, consumption: ResourceConsumption) -> None:
        raise OSError("accounting store unavailable")


def test_commit_failures_end_in_a_recorded_failed_outcome() -> None:
    runner = make_runner(allocation_factory=lambda run_identity: _FailingReportAllocation())
    result, handle = _run_count(runner, _MeteringExecutor())
    assert result.value.startswith("commit_failed:")
    outcomes = handle.ledger.select(NaturalExecutionOutcomeEvent)
    assert len(outcomes) == 1
    assert isinstance(outcomes[0].event, NaturalExecutionOutcomeEvent)
    assert outcomes[0].event.status == "failure"
    assert outcomes[0].event.failure_kind == "commit_failed"


@dataclass
class _StreamedAgentResult:
    output: object
    usage: object


class _StreamingAgent(Agent[nh.StepContext, Any]):
    """Drive streaming while preserving the Core-supplied output and admission capabilities."""

    async def run(self, *arguments: Any, **keyword_arguments: Any) -> _StreamedAgentResult:  # pyright: ignore[reportIncompatibleMethodOverride]
        async with self.run_stream(*arguments, **keyword_arguments) as stream:
            output = await stream.get_output()
            return _StreamedAgentResult(output=output, usage=stream.usage)


def test_streamed_model_requests_are_admitted_and_accounted_per_step() -> None:
    from pydantic_ai.models.test import TestModel

    from nighthawk.runtime.model_outcome import ModelStepResponse
    from nighthawk.runtime.step_context import StepContext

    agent = _StreamingAgent(
        model=TestModel(call_tools=["nh_evaluate"], custom_output_args={"result": {"kind": "pass"}}),
        output_type=ModelStepResponse,
        deps_type=StepContext,
    )
    executor = nh.AgentStepExecutor.from_agent(agent=agent)
    result, handle = _run_count(make_runner(accepts_aggregate_executor_accounting=False), executor)
    assert result.value == "0"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.executor_step_count == 2
    assert outcome.executor_usage.total_token_count > 0

    bounded = make_runner(
        accepts_aggregate_executor_accounting=False,
        allocation_factory=lambda run_identity: bounded_resource_allocation(maximum_total_token_count=1),
    )
    result, handle = _run_count(bounded, executor)
    assert result.value.startswith("allocation_exhausted:")
    exhaustion = handle.ledger.select(ResourceExhaustionEvent)[0].event
    assert isinstance(exhaustion, ResourceExhaustionEvent)
    assert exhaustion.reason == "total_token_count_exhausted"
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.executor_step_count == 1
    assert outcome.executor_usage.request_count == 1


def test_run_entry_records_the_executor_step_observation_mode() -> None:
    from nighthawk.agent import RunEntryEvent

    _result, handle = _run_count(make_runner(), ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"count": 1})]))
    run_entry = handle.ledger.entries[0].event
    assert isinstance(run_entry, RunEntryEvent)
    assert run_entry.executor_step_observation == "aggregate"
    _result, handle = _run_count(make_runner(accepts_aggregate_executor_accounting=False), _core_agent_executor([]))
    run_entry = handle.ledger.entries[0].event
    assert isinstance(run_entry, RunEntryEvent)
    assert run_entry.executor_step_observation == "model_requests"
