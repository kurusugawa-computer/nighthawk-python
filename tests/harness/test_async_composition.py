from __future__ import annotations

import asyncio

import pytest

from nighthawk.core.scoping import get_scope
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.usage import ExecutionUsage
from nighthawk.harness.context import current_capabilities, current_grant, scope
from nighthawk.harness.dispatch import AsyncGeneratedCallableReference, dispatch
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import CapabilityInvocationEvent, DispatchEvent, DispatchOutcomeEvent, NaturalExecutionOutcomeEvent
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.run_handle import RunHandle
from tests.support.harness import make_runner
from tests.support.runs import EchoInput, EchoResult, run_body


class _CooperativeExecutor:
    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        assert set(current_capabilities()) == {"meet"}
        result = await current_capabilities()["meet"]()  # type: ignore[operator]
        meter = get_scope().usage_meter
        assert meter is not None
        meter.record(ExecutionUsage(request_count=1, input_token_count=7, output_token_count=1))
        return StepCandidate(outcome=Pass(), binding_name_to_value={"result": result})


def test_async_natural_generated_branches_share_references_and_join() -> None:
    barrier = asyncio.Barrier(2)
    shared: list[int] = []
    received: list[object] = []

    async def meet() -> int:
        received.append(shared)
        shared.append(len(shared))
        await asyncio.wait_for(barrier.wait(), timeout=5)
        return len(shared)

    def unused() -> int:
        raise AssertionError("The discovery/action scope must omit this capability")

    meeting = Capability(name="meet", function=meet, authority_boundary="cooperative test barrier")
    grant = Grant.of(meeting, Capability(name="unused", function=unused, authority_boundary="not selected"), shared=shared)
    source = '''import nighthawk as nh

@nh.function
async def inspect_shared(value: object) -> int:
    assert value is shared
    result: int = 0
    """execute
    Inspect <value> through the meeting capability and set <:result>.
    """
    return result
'''

    async def branches(function: AsyncGeneratedCallableReference) -> tuple[object, object]:
        async with asyncio.TaskGroup() as group:
            first = group.create_task(function(shared))
            second = group.create_task(function(shared))
        return first.result(), second.result()

    def body(run_input: EchoInput) -> EchoResult:
        with scope(current_grant().restrict(("meet", "shared"))):
            function = dispatch(
                CodeProposal(
                    source=source,
                    export_name_tuple=("inspect_shared",),
                    effect_footprint=EffectFootprint(capability_name_set=frozenset({"meet"})),
                    dependency_tuple=(
                        DependencyDeclaration(name="meet", kind="capability", declared_type=meeting.declared_type),
                        DependencyDeclaration(name="shared", kind="reference", declared_type=list),
                    ),
                )
            ).export_name_to_value["inspect_shared"]
            assert isinstance(function, AsyncGeneratedCallableReference)
            assert asyncio.run(branches(function)) == (2, 2)
        assert current_grant().capabilities["unused"] is grant.capabilities["unused"]
        return EchoResult(value=str(shared))

    handle = RunHandle()
    result = run_body(body, runner=make_runner(), step_executor=_CooperativeExecutor(), run_entry_grant=grant, handle=handle)
    assert result.value == "[0, 1]"
    assert len(received) == 2 and all(value is shared for value in received)
    natural_outcomes = [entry.event for entry in handle.ledger.entries if isinstance(entry.event, NaturalExecutionOutcomeEvent)]
    assert len(natural_outcomes) == 2
    assert len({event.attribution_node_id for event in natural_outcomes}) == 2
    assert all(event.executor_usage.input_token_count == 7 for event in natural_outcomes)
    assert handle.ledger.executor_step_count() == 2
    invocation_entries = [
        entry for entry in handle.ledger.select(DispatchEvent) if isinstance(entry.event, DispatchEvent) and entry.event.dispatch_kind == "invocation"
    ]
    outcome_entries = handle.ledger.select(DispatchOutcomeEvent)
    assert len(invocation_entries) == 2 and len(outcome_entries) == 3
    assert all(isinstance(entry.event, DispatchOutcomeEvent) and entry.event.status == "success" for entry in outcome_entries)
    capability_entries = handle.ledger.select(CapabilityInvocationEvent)
    assert len(capability_entries) == 2
    assert max(entry.sequence for entry in invocation_entries) < min(entry.sequence for entry in capability_entries)
    assert handle.ledger.entries[-1].event.kind == "run_end"


def test_cancelled_generated_invocations_finish_their_ledger_outcomes() -> None:
    barrier = asyncio.Barrier(2)
    joined = []

    async def action(value: int) -> int:
        await asyncio.wait_for(barrier.wait(), timeout=5)
        if value == 0:
            raise TypeError("unexpected analysis failure")
        try:
            await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
        finally:
            joined.append(value)
        return value

    capability = Capability(name="action", function=action, authority_boundary="cooperative test")
    proposal = CodeProposal(
        source="async def call(value: int) -> int:\n    return await action(value)\n",
        export_name_tuple=("call",),
        dependency_tuple=(DependencyDeclaration(name="action", kind="capability", declared_type=capability.declared_type),),
        effect_footprint=EffectFootprint(capability_name_set=frozenset({"action"})),
    )

    async def work(function: AsyncGeneratedCallableReference) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(function(0))
            group.create_task(function(1))

    def body(run_input: EchoInput) -> EchoResult:
        function = dispatch(proposal).export_name_to_value["call"]
        assert isinstance(function, AsyncGeneratedCallableReference)
        with pytest.raises(ExceptionGroup) as failure:
            asyncio.run(work(function))
        assert all(isinstance(error, TypeError) for error in failure.value.exceptions)
        return EchoResult()

    handle = RunHandle()
    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(capability), handle=handle)
    assert joined == [1]
    outcome_list = [entry.event for entry in handle.ledger.entries if isinstance(entry.event, DispatchOutcomeEvent)]
    assert {outcome.failure_class_name for outcome in outcome_list} == {None, "TypeError", "CancelledError"}
    capability_outcomes = [entry.event for entry in handle.ledger.entries if isinstance(entry.event, CapabilityInvocationEvent)]
    assert {outcome.failure_class_name for outcome in capability_outcomes} == {"TypeError", "CancelledError"}
