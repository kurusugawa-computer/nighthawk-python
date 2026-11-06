import asyncio
from dataclasses import replace
from pathlib import Path

from examples.async_object_workflow import AnalysisRequest, BatchFailure, BatchFinding, SampleBatch, WorkflowInput, analyze_batches, propose_analysis
from examples.continued_object_workflow import continue_workflow, decode_workflow_handoff, encode_workflow_handoff, make_handoff
from examples.evaluate_object_workflow import evaluate_workflow
from examples.learning_loop import LearningLoopInput, build_learning_validation, learning_loop
from nighthawk.adapters.artifacts import DirectoryArtifactStore
from nighthawk.core.step_executor import StepExecutor
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import CallbackExecutor
from nighthawk.harness.context import current_capabilities, current_grant, current_run_ledger, scope
from nighthawk.harness.evidence import analyze_run
from nighthawk.harness.grants import Grant
from nighthawk.harness.ledger import DispatchEvent
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.run_contract import RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.parts.artifacts import SaveArtifactRequest, StoredArtifactDescriptor, build_artifact_storage
from nighthawk.parts.procedures import materialize_procedure, select_procedure
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedurePage, ProcedureQuery, ProcedureSelection, build_procedure_discovery
from nighthawk.parts.values import select_sequence_window
from tests.examples.workflow_support import ANALYSIS_SOURCE, analysis_executor, learning_handler, sample_batches
from tests.support.harness import make_runner


def combined_scenario(
    tmp_path: Path,
    *,
    initial_executor: StepExecutor | None = None,
    continuation_executor: StepExecutor | None = None,
    runner: Runner | None = None,
) -> tuple[RunHandle, RunHandle]:
    selected_runner = runner if runner is not None else make_runner()

    catalog = ProcedureCatalog()
    indeterminate_attempt_counts = []
    selected_identity_list = []
    partial_report_list = []

    class ObservedArtifactStore(DirectoryArtifactStore):
        def put_text(self, chunks, *, media_type: str) -> StoredArtifactDescriptor:
            pending = analyze_run(current_run_ledger()).unmatched_effect_attempt_tuple
            indeterminate_attempt_counts.append(len(pending))
            assert pending
            return super().put_text(chunks, media_type=media_type)

    store = ObservedArtifactStore(tmp_path)
    grant = Grant.compose(
        build_procedure_discovery(catalog=catalog),
        build_learning_validation(),
        build_artifact_storage(store=store, maximum_payload_character_count=20000, allow_write=True),
    )
    first_batch = sample_batches()[0].model_copy(update={"sample_tuple": (*range(1000), -2)})
    run_input = WorkflowInput(batch_tuple=(first_batch, *sample_batches()[1:]))
    observed_lessons = []

    def handler(call: StepRequest) -> StepCandidate:
        if "selection" in call.output_binding_names:
            page = current_capabilities()["query_procedures"](ProcedureQuery(text="samples"))
            assert isinstance(page, ProcedurePage)
            return StepCandidate(
                binding_name_to_value={
                    "selection": ProcedureSelection(procedure_id=page.descriptor_tuple[0].procedure_id, reason="Indexed numeric analysis")
                }
            )
        if "request" in call.step_context.step_locals:
            observed_lessons.extend(call.step_context.step_locals["request"].lesson_tuple)  # type: ignore[attr-defined]
            return StepCandidate(binding_name_to_value={"proposal": CodeProposal(source=ANALYSIS_SOURCE, export_name_tuple=("analyze",))})
        return learning_handler(call)

    def body(value: WorkflowInput) -> StoredArtifactDescriptor:
        assert -2 not in select_sequence_window(value.batch_tuple[0].sample_tuple).item_tuple
        assert select_sequence_window(value.batch_tuple[0].sample_tuple, starting_index=1000).item_tuple == (-2,)
        learned = learning_loop(LearningLoopInput())
        assert learned.learning.lesson_tuple
        assert learned.trial_tuple[1].lesson_input_tuple == learned.learning.lesson_tuple
        assert learned.trial_tuple[1].selected_value_tuple == (-2,)
        working_input = value.model_copy(update={"lesson_tuple": learned.learning.lesson_tuple})
        request = AnalysisRequest(
            batch=value.batch_tuple[0], criterion=value.criterion, lesson_tuple=learned.learning.lesson_tuple, configuration=value.configuration
        )
        with scope(Grant.EMPTY):
            proposal = asyncio.run(propose_analysis(request))
            materialize_procedure(catalog, proposal, export_name="analyze", purpose="Select indexed samples")
        with scope(current_grant().restrict(("query_procedures",))):
            selection = select_procedure("Find indexed samples below zero")
            selected_identity_list.append(selection.procedure_id)
        report = asyncio.run(
            analyze_batches(working_input.model_copy(update={"batch_tuple": value.batch_tuple[:2]}), catalog=catalog, selection=selection)
        )
        # Controlled ordinary failure: the neighboring successful branch must survive.
        fixture_catalog = ProcedureCatalog()
        fixture_selection = materialize_procedure(
            fixture_catalog,
            CodeProposal(
                source="async def analyze(samples: tuple[int, ...], criterion: int) -> tuple[tuple[int, int], ...]:\n    if samples[0] == 0:\n        raise ValueError('controlled ordinary failure')\n    return ((0, samples[0]),)\n",
                export_name_tuple=("analyze",),
            ),
            export_name="analyze",
            purpose="Controlled partial failure fixture",
        )
        partial_report = asyncio.run(
            analyze_batches(
                WorkflowInput(batch_tuple=(SampleBatch(batch_id="failed", sample_tuple=(0,)), SampleBatch(batch_id="preserved", sample_tuple=(-1,)))),
                catalog=fixture_catalog,
                selection=fixture_selection,
            )
        )
        assert isinstance(partial_report.result_tuple[0], BatchFailure)
        assert isinstance(partial_report.result_tuple[1], BatchFinding)
        partial_report_list.append(partial_report)
        handoff = make_handoff(working_input, report, task_id="combined", objective="strict negatives", pending_batch_tuple=value.batch_tuple[2:])
        return current_capabilities()["save_artifact"](SaveArtifactRequest(text=encode_workflow_handoff(handoff), media_type="application/json"))  # type: ignore[operator, return-value]

    handle = RunHandle()
    composition = ScaffoldComposition(
        run_contract=RunContract(input_type=WorkflowInput, result_type=StoredArtifactDescriptor),
        body=body,
        run_entry_grant=grant,
        composition_perimeter_policy=PerimeterPolicy.permitting_all(),
    )
    descriptor = replace(selected_runner, step_executor=initial_executor if initial_executor is not None else CallbackExecutor(handler)).run(
        composition, run_input, handle=handle
    )
    restored = decode_workflow_handoff(
        DirectoryArtifactStore(tmp_path).load_text(descriptor, maximum_character_count=20000), expected_task_id="combined"
    )
    assert restored.lesson_tuple and restored.selected_evidence_tuple
    if initial_executor is None:
        assert observed_lessons == list(restored.lesson_tuple)
    invocation_ids = [
        entry.event.definition_id
        for entry in handle.ledger.entries
        if isinstance(entry.event, DispatchEvent) and entry.event.dispatch_kind == "invocation"
    ]
    assert invocation_ids.count(selected_identity_list[0]) == 2
    analysis = analyze_run(handle.ledger)
    assert not analysis.active_node_id_tuple and not analysis.unmatched_effect_attempt_tuple
    continuation = RunHandle()
    report = asyncio.run(
        continue_workflow(
            restored,
            run_entry_grant=Grant.EMPTY,
            handle=continuation,
            runner=replace(selected_runner, step_executor=continuation_executor if continuation_executor is not None else analysis_executor(1)),
        )
    )
    assert continuation.run_id != handle.run_id
    assert [result.selected_index_value_tuple for result in report.result_tuple if isinstance(result, BatchFinding)] == [
        ((1000, -2),),
        ((0, -3),),
        ((1, -5),),
    ]
    assert evaluate_workflow(run_input, report, ledger_tuple=(handle.ledger, continuation.ledger)).correct

    print(
        {
            "retained_value_beyond_first_page": first_batch.sample_tuple[1000],
            "reused_definition_id": selected_identity_list[0],
            "partial_failure": tuple(type(result).__name__ for result in partial_report_list[0].result_tuple),
            "lesson_count": len(restored.lesson_tuple),
            "indeterminate_attempt_count_during_save": indeterminate_attempt_counts[0],
            "initial_run_id": handle.run_id,
            "continuation_run_id": continuation.run_id,
            "final_finding_count": len(report.result_tuple),
        }
    )
    return handle, continuation
