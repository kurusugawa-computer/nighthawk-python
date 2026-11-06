"""Finite real-model composition acceptance with explicitly limited runner doubles.

These tests establish model integration and host mediation, not production
confinement or forced cessation. Credentials must be supplied explicitly.
"""

import os
from dataclasses import replace
from pathlib import Path

import pytest

import nighthawk as nh
from examples.procedure_discovery import DiscoveryInput, run_procedure_discovery
from nighthawk.agent import CapabilityInvocationEvent, DispatchEvent, NaturalExecutionOutcomeEvent, RunEntryEvent, RunHandle
from nighthawk.agent.catalog import build_procedure_discovery
from nighthawk.agent.runtime import bounded_resource_allocation
from tests.nighthawk.agent.conftest import make_runner
from tests.nighthawk.agent.procedure_support import make_catalog

pytestmark = [pytest.mark.integration, pytest.mark.skipif("OPENAI_API_KEY" not in os.environ, reason="Live acceptance requires OPENAI_API_KEY")]

DISCOVERY_MODEL = "openai-responses:gpt-6-luna"
LEARNING_MODEL = "openai-responses:gpt-6.1-sol"


def live_executor(*, model: str = DISCOVERY_MODEL) -> nh.AgentStepExecutor:
    return nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=model, execution_model_settings={"max_tokens": 4000}))


def live_runner(*, maximum_executor_step_count: int = 24, maximum_total_token_count: int = 60000):
    return make_runner(
        accepts_aggregate_executor_accounting=False,
        allocation_factory=lambda identity: bounded_resource_allocation(
            identity=f"live-{identity.run_id}",
            maximum_executor_step_count=maximum_executor_step_count,
            maximum_total_token_count=maximum_total_token_count,
            maximum_dispatch_count=20,
            maximum_natural_execution_count=10,
        ),
    )


def record_live_evidence(
    handle: RunHandle, *, model: str = DISCOVERY_MODEL, maximum_executor_step_count: int = 24, maximum_total_token_count: int = 60000
) -> None:
    outcomes = [entry.event for entry in handle.ledger.entries if isinstance(entry.event, NaturalExecutionOutcomeEvent)]
    entry = next(entry.event for entry in handle.ledger.entries if isinstance(entry.event, RunEntryEvent))
    print(
        {
            "model": model,
            "run_id": handle.ledger.run_id,
            "observation": entry.executor_step_observation,
            "maximum_executor_step_count": maximum_executor_step_count,
            "maximum_total_token_count": maximum_total_token_count,
            "maximum_output_tokens": 4000,
            "request_count": sum(outcome.executor_usage.request_count for outcome in outcomes),
            "total_token_count": sum(outcome.executor_usage.total_token_count for outcome in outcomes),
            "fixture": "confinement and termination doubles; real provider and host mediation",
        }
    )


def test_live_procedure_discovery() -> None:
    catalog = make_catalog()
    handle = RunHandle()
    result = run_procedure_discovery(
        DiscoveryInput(objective="Find a procedure to square integers, then wrap it for reuse", value_tuple=(3, 7)),
        catalog=catalog,
        run_entry_grant=build_procedure_discovery(catalog=catalog),
        handle=handle,
        runner=replace(live_runner(), step_executor=live_executor()),
    )
    record_live_evidence(handle)
    assert result.selection.procedure_id == "square"
    assert result.result_tuple == (9, 49) and result.proposal.source
    assert any(
        isinstance(entry.event, CapabilityInvocationEvent) and entry.event.capability_name == "query_procedures" and entry.event.status == "success"
        for entry in handle.ledger.entries
    )
    invocations = [
        entry.event for entry in handle.ledger.entries if isinstance(entry.event, DispatchEvent) and entry.event.dispatch_kind == "invocation"
    ]
    assert len(invocations) == 2 and invocations[0].definition_id == invocations[1].definition_id


def test_live_learning_loop() -> None:
    from examples.learning_loop import LearningLoopInput, build_learning_validation, run_learning_loop

    handle = RunHandle()
    result = run_learning_loop(
        LearningLoopInput(),
        run_entry_grant=build_learning_validation(),
        handle=handle,
        runner=replace(live_runner(), step_executor=live_executor(model=LEARNING_MODEL)),
    )
    record_live_evidence(handle, model=LEARNING_MODEL)
    first, second = result.trial_tuple
    assert first.selected_value_tuple == (-2, 0) and not first.matches_criterion
    assert result.learning.lesson_tuple
    assert all(lesson.evidence_reference_tuple for lesson in result.learning.lesson_tuple)
    assert second.lesson_input_tuple == result.learning.lesson_tuple
    assert second.selected_value_tuple == (-2,) and second.matches_criterion
    supplied_event_ids = {entry.event_id for entry in handle.ledger.entries if isinstance(entry.event, CapabilityInvocationEvent)}
    assert all(
        reference.run_id == handle.ledger.run_id and reference.event_id in supplied_event_ids
        for lesson in result.learning.lesson_tuple
        for reference in lesson.evidence_reference_tuple
    )


def test_live_composed_workflow(tmp_path: Path) -> None:
    from tests.nighthawk.agent.composed_support import combined_scenario

    initial, continuation = combined_scenario(
        tmp_path,
        initial_executor=live_executor(model=LEARNING_MODEL),
        continuation_executor=live_executor(),
        runner=live_runner(maximum_executor_step_count=48, maximum_total_token_count=120000),
    )
    record_live_evidence(initial, model=LEARNING_MODEL, maximum_executor_step_count=48, maximum_total_token_count=120000)
    record_live_evidence(continuation, maximum_executor_step_count=48, maximum_total_token_count=120000)
