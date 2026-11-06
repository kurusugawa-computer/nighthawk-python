from dataclasses import replace

from examples.retained_text_workflow import generate_normalizer, run_normalization_trial
from nighthawk.core.step_request import StepCandidate
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.ledger import DefinitionMaterializationEvent, DispatchEvent, NaturalExecutionOutcomeEvent
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.run_handle import RunHandle
from tests.support.harness import make_runner


async def test_one_generation_serves_multiple_evaluated_runs_without_approval() -> None:
    proposal = CodeProposal(
        source="def normalize_labels(labels: tuple[str, ...]) -> tuple[str, ...]:\n    return tuple(dict.fromkeys(label.strip().casefold() for label in labels if label.strip()))\n",
        export_name_tuple=("normalize_labels",),
    )
    runner = replace(make_runner(), step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"proposal": proposal})]))
    generation = RunHandle()
    function = await generate_normalizer(runner=runner, handle=generation)
    assert len(generation.ledger.select(NaturalExecutionOutcomeEvent)) == 1
    assert len(generation.ledger.select(DispatchEvent)) == 1
    for labels in ((" Alpha ", "alpha", "BETA", ""), (" X ", "x", "Y", " ")):
        handle = RunHandle()
        trial = run_normalization_trial(function, labels, runner=runner, handle=handle)
        assert trial.matches_criterion
        assert trial.run_id != generation.run_id
        assert not handle.ledger.select(NaturalExecutionOutcomeEvent)
        events = handle.ledger.select(DefinitionMaterializationEvent)
        assert len(events) == 1
        definition = events[0].event
        assert isinstance(definition, DefinitionMaterializationEvent)
        assert definition.retained_definition_id == function.definition.definition_id
        assert definition.source_provenance_class == "generated" and definition.approval_identity is None


async def test_application_evaluation_can_reject_successfully_executed_source() -> None:
    async def incorrect(request: object, context: object) -> CodeProposal:
        return CodeProposal(
            source="def normalize_labels(labels: tuple[str, ...]) -> tuple[str, ...]:\n    return labels\n", export_name_tuple=("normalize_labels",)
        )

    runner = make_runner()
    function = await generate_normalizer(runner=runner, generate_function=incorrect)
    trial = run_normalization_trial(function, (" X ", "x"), runner=runner)
    assert not trial.matches_criterion
    assert trial.result == (" X ", "x") and trial.expected == ("x",)
