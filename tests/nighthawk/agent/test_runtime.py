"""Public runtime admission, cooperative completion, and cleanup reattachment."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from typing import cast

import pytest

from nighthawk.agent import (
    CodeProposal,
    DependencyDeclaration,
    EffectFootprint,
    ExecutorUsage,
    GeneratedCallableReference,
    Grant,
    PerimeterPolicy,
    RunEndEvent,
    RunEntryEvent,
    RunFailedError,
    RunRejectedError,
    RunTerminationError,
    current_run_ledger,
    dispatch,
)
from nighthawk.agent.runtime import (
    AsyncScaffoldComposition,
    ConfinementRequest,
    ResourceConsumption,
    ResourceRequest,
    RunContract,
    RunHandle,
    Runner,
    ScaffoldComposition,
    TerminationRequest,
    bounded_resource_allocation,
    unconfirmed_perimeter_confinement,
)
from nighthawk.runtime.step_request import StepCandidate
from nighthawk.testing import ScriptedExecutor

from .conftest import ConfirmedConfinement


def make_runner() -> Runner:
    return Runner(
        step_executor=ScriptedExecutor(default_response=StepCandidate()),
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(),
        accepts_aggregate_executor_accounting=True,
    )


async def test_public_runner_admits_only_on_await_and_allocates_each_run() -> None:
    runner = make_runner()
    first = RunHandle()
    second = RunHandle()
    barrier = asyncio.Barrier(2)

    async def body(value: int) -> int:
        ledger = current_run_ledger()
        await asyncio.wait_for(barrier.wait(), timeout=5)
        assert current_run_ledger() is ledger
        assert ledger.select(RunEndEvent) == ()
        return value + 1

    composition = AsyncScaffoldComposition(RunContract(int, int), body)
    pending = runner.run(composition, 1, run_id="explicit-async-run", handle=first)
    assert first.run_id is None
    assert await asyncio.gather(pending, runner.run(composition, 2, handle=second)) == [2, 3]
    assert first.run_id != second.run_id
    assert first.run_id == "explicit-async-run"
    first_entry = first.ledger.select(RunEntryEvent)[0].event
    second_entry = second.ledger.select(RunEntryEvent)[0].event
    assert isinstance(first_entry, RunEntryEvent) and isinstance(second_entry, RunEntryEvent)
    assert first_entry.resource_allocation_identity != second_entry.resource_allocation_identity
    assert len(first.ledger.select(RunEndEvent)) == len(second.ledger.select(RunEndEvent)) == 1
    synchronous = RunHandle()
    assert runner.run(ScaffoldComposition(RunContract(int, int), lambda value: value + 1), 4, run_id="explicit-sync-run", handle=synchronous) == 5
    assert synchronous.run_id == "explicit-sync-run"
    assert len(synchronous.ledger.select(RunEndEvent)) == 1


async def test_caller_cancellation_records_terminal_after_body_cleanup() -> None:
    runner = make_runner()
    handle = RunHandle()
    entered = asyncio.Event()
    cleaned: list[bool] = []

    async def body(value: None) -> int:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            assert handle.ledger.select(RunEndEvent) == ()
            cleaned.append(True)
        return 1

    task = asyncio.create_task(runner.run(AsyncScaffoldComposition(RunContract(type(None), int), body), None, handle=handle))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned == [True]
    terminal = handle.ledger.select(RunEndEvent)[0].event
    assert isinstance(terminal, RunEndEvent)
    assert terminal.failure_class_name == "CancelledError"


def test_result_validation_and_default_admission_refusal() -> None:
    runner = make_runner()
    handle = RunHandle()
    with pytest.raises(RunFailedError, match="declared result type"):
        runner.run(ScaffoldComposition(RunContract(int, int), lambda value: cast(int, "invalid")), 1, handle=handle)
    assert len(handle.ledger.select(RunEndEvent)) == 1
    strict_runner = Runner(
        step_executor=runner.step_executor,
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=runner.resource_allocation_factory,
    )
    rejected = RunHandle()
    with pytest.raises(RunRejectedError):
        strict_runner.run(ScaffoldComposition(RunContract(int, int), lambda value: value), 1, handle=rejected)
    assert rejected.run_id is None


@pytest.mark.parametrize("body_fails", [False, True])
def test_pending_cleanup_waits_for_actual_branch_completion_without_restarting_body(body_fails: bool) -> None:
    release = threading.Event()
    entered = threading.Event()
    thread_list: list[threading.Thread] = []
    body_call_list: list[int] = []
    handle = RunHandle()
    runner = Runner(
        step_executor=ScriptedExecutor(default_response=StepCandidate()),
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(),
        perimeter_confinement=ConfirmedConfinement(),
        accepts_aggregate_executor_accounting=True,
    )

    def body(value: int) -> int:
        body_call_list.append(value)
        result = dispatch(
            CodeProposal(
                source="def block() -> int:\n    entered.set()\n    release.wait()\n    return 1\n",
                export_name_tuple=("block",),
                dependency_tuple=(
                    DependencyDeclaration(name="entered", kind="reference", declared_type=threading.Event),
                    DependencyDeclaration(name="release", kind="reference", declared_type=threading.Event),
                ),
            )
        )
        function = cast(GeneratedCallableReference, result.export_name_to_value["block"])
        context = contextvars.copy_context()
        thread = threading.Thread(target=lambda: context.run(function))
        thread_list.append(thread)
        thread.start()
        assert entered.wait(timeout=5)
        if body_fails:
            raise ValueError("original body failure")
        return value

    try:
        with pytest.raises(RunTerminationError):
            runner.run(
                ScaffoldComposition(
                    RunContract(int, int),
                    body,
                    run_entry_grant=Grant.of(entered=entered, release=release),
                ),
                1,
                handle=handle,
            )
        assert not handle.terminate()
        assert not handle.terminate()
        with pytest.raises(RunTerminationError):
            handle._finish_pending()
        assert handle.ledger.select(RunEndEvent) == ()
    finally:
        release.set()
        for thread in thread_list:
            thread.join(timeout=5)
            assert not thread.is_alive()
    handle._finish_pending()
    handle._finish_pending()
    assert handle.terminate()
    assert body_call_list == [1]
    terminals = handle.ledger.select(RunEndEvent)
    assert len(terminals) == 1
    terminal = terminals[0].event
    assert isinstance(terminal, RunEndEvent)
    assert terminal.terminal_kind == "host_termination"
    assert terminal.failure_class_name == ("ValueError" if body_fails else None)


def _request(kind: str, depth: int = 0) -> ResourceRequest:
    return ResourceRequest(run_id="run", kind=kind, attribution_node_id="node", recursion_depth=depth)  # type: ignore[arg-type]


def test_bounded_allocation_checks_recursion_depth_and_dispatch_count() -> None:
    allocation = bounded_resource_allocation(maximum_recursion_depth=1, maximum_dispatch_count=2, identity="a")
    assert allocation.identity == "a"
    assert allocation.admit(_request("dispatch", depth=1)) is None
    rejection = allocation.admit(_request("dispatch", depth=2))
    assert rejection is not None and rejection.reason == "recursion_depth_exhausted"
    assert allocation.admit(_request("dispatch", depth=1)) is None
    rejection = allocation.admit(_request("dispatch", depth=1))
    assert rejection is not None and rejection.reason == "dispatch_count_exhausted"


def test_bounded_allocation_admits_executor_steps_individually_and_surfaces_token_exhaustion_at_the_next_seam() -> None:
    allocation = bounded_resource_allocation(maximum_executor_step_count=2, maximum_total_token_count=100)
    assert allocation.admit(_request("natural_execution")) is None
    assert allocation.admit(_request("executor_step")) is None
    assert allocation.admit(_request("executor_step")) is None
    rejection = allocation.admit(_request("executor_step"))
    assert rejection is not None and rejection.reason == "executor_step_count_exhausted"
    allocation.record(
        ResourceConsumption(
            run_id="run",
            kind="natural_execution",
            attribution_node_id="node",
            executor_step_count=2,
            executor_usage=ExecutorUsage(request_count=2, total_token_count=10),
        )
    )
    rejection = allocation.admit(_request("natural_execution"))
    assert rejection is not None and rejection.reason == "executor_step_count_exhausted"
    tokens = bounded_resource_allocation(maximum_total_token_count=5)
    tokens.record(
        ResourceConsumption(run_id="run", kind="natural_execution", attribution_node_id="node", executor_usage=ExecutorUsage(total_token_count=5))
    )
    rejection = tokens.admit(_request("natural_execution"))
    assert rejection is not None and rejection.reason == "total_token_count_exhausted"


def test_bounded_allocation_counts_natural_executions() -> None:
    allocation = bounded_resource_allocation(maximum_natural_execution_count=1)
    assert allocation.admit(_request("natural_execution")) is None
    rejection = allocation.admit(_request("natural_execution"))
    assert rejection is not None and rejection.reason == "natural_execution_count_exhausted"


def test_defaults_execute_in_process_without_claiming_confinement_or_forced_termination() -> None:
    runner = Runner(
        step_executor=ScriptedExecutor(default_response=StepCandidate()),
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=lambda run_identity: bounded_resource_allocation(),
    )
    request = ConfinementRequest(run_id="run", submitting_node_id="root", recursion_depth=1, effect_footprint=EffectFootprint())
    assert runner.perimeter_confinement is None
    assert unconfirmed_perimeter_confinement().confirm(request) is None
    report = runner.termination_mechanism.terminate(
        TerminationRequest(run_id="run", requesting_node_id="root", target_node_id="node", affected_node_id_tuple=("node",))
    )
    assert report.ceased is False


def test_executor_usage_adds_fieldwise() -> None:
    total = ExecutorUsage(request_count=1, total_token_count=3) + ExecutorUsage(request_count=2, input_token_count=4)
    assert total == ExecutorUsage(request_count=3, input_token_count=4, total_token_count=3)
