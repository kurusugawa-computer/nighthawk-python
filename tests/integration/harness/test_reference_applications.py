from dataclasses import replace
from pathlib import Path

from examples.procedure_discovery import DiscoveryInput, run_procedure_discovery
from examples.retained_text_workflow import generate_normalizer, run_normalization_trial
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.harness.ledger import CapabilityInvocationEvent, DispatchEvent, NaturalExecutionOutcomeEvent, RunEntryEvent
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.parts.procedures.catalog import build_procedure_discovery
from tests.support.harness import make_runner
from tests.support.procedures import make_catalog

DISCOVERY_MODEL = "openai-responses:gpt-6-luna"
LEARNING_MODEL = "openai-responses:gpt-6.1-sol"


def live_executor(*, model: str = DISCOVERY_MODEL) -> AgentStepExecutor:
    return AgentStepExecutor(configuration=StepExecutorConfiguration(execution_model=model, execution_model_settings={"max_tokens": 4000}))


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
    from tests.examples.composed_support import combined_scenario

    initial, continuation = combined_scenario(
        tmp_path,
        initial_executor=live_executor(model=LEARNING_MODEL),
        continuation_executor=live_executor(),
        runner=live_runner(maximum_executor_step_count=48, maximum_total_token_count=120000),
    )
    record_live_evidence(initial, model=LEARNING_MODEL, maximum_executor_step_count=48, maximum_total_token_count=120000)
    record_live_evidence(continuation, maximum_executor_step_count=48, maximum_total_token_count=120000)


async def test_live_generate_once_and_reuse_in_fresh_batch_runs() -> None:
    runner = Runner(
        step_executor=AgentStepExecutor(
            StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna", execution_model_settings={"max_tokens": 4000})
        ),
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(maximum_executor_step_count=16, maximum_total_token_count=40000),
    )
    generation = RunHandle()
    function = await generate_normalizer(runner=runner, handle=generation)
    assert len(generation.ledger.select(NaturalExecutionOutcomeEvent)) == 1
    for labels in ((" Alpha ", "alpha", "BETA", ""), (" X ", "x", "Y", " ")):
        handle = RunHandle()
        trial = run_normalization_trial(function, labels, runner=runner, handle=handle)
        assert trial.matches_criterion
        assert trial.run_id != generation.run_id
        assert not handle.ledger.select(NaturalExecutionOutcomeEvent)
