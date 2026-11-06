"""Select domain state for explicit text persistence and a fresh run."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from examples.async_object_workflow import (
    AnalysisConfiguration,
    BatchFailure,
    BatchFinding,
    BatchReport,
    SampleBatch,
    WorkflowInput,
    analyze_batches,
    propose_analysis,
    run_async_object_workflow,
)
from nighthawk.harness.context import current_run_ledger
from nighthawk.harness.grants import Grant
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.parts.evidence import EvidenceBounds, EvidenceItem, EvidenceQuery, project_evidence
from nighthawk.parts.learning import Lesson
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureSelection


class WorkflowHandoff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: int = 1
    task_id: str
    originating_run_id: str
    objective: str
    criterion: int
    configuration_label: str
    completed_finding_tuple: tuple[BatchFinding, ...] = ()
    pending_batch_tuple: tuple[SampleBatch, ...] = ()
    failed_batch_tuple: tuple[tuple[SampleBatch, BatchFailure], ...] = ()
    lesson_tuple: tuple[Lesson, ...] = ()
    selected_evidence_tuple: tuple[EvidenceItem, ...] = ()

    @model_validator(mode="after")
    def _validate_scheduling(self) -> WorkflowHandoff:
        if self.format_version != 1:
            raise ValueError("Unsupported handoff format version")
        completed_ids = [finding.batch_id for finding in self.completed_finding_tuple]
        pending_ids = [batch.batch_id for batch in self.pending_batch_tuple]
        failed_ids = [batch.batch_id for batch, failure in self.failed_batch_tuple]
        if any(batch.batch_id != failure.batch_id for batch, failure in self.failed_batch_tuple):
            raise ValueError("Failed batch identity does not match its failure")
        all_ids = completed_ids + pending_ids + failed_ids
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Completed, pending, and failed batch identities must be unique and disjoint")
        return self


def encode_workflow_handoff(handoff: WorkflowHandoff) -> str:
    """Encode selected domain values only, without executable authority or aliases."""
    return handoff.model_dump_json()


def decode_workflow_handoff(text: str, *, expected_task_id: str) -> WorkflowHandoff:
    """Construct declared domain types, never classes or source named by data."""
    handoff = WorkflowHandoff.model_validate_json(text)
    if handoff.task_id != expected_task_id:
        raise ValueError("Handoff task identity does not match the expected task")
    return handoff


def make_handoff(
    run_input: WorkflowInput, report: BatchReport, *, task_id: str, objective: str, pending_batch_tuple: tuple[SampleBatch, ...]
) -> WorkflowHandoff:
    batch_id_to_batch = {batch.batch_id: batch for batch in run_input.batch_tuple}
    result_id_tuple = tuple(result.batch_id for result in report.result_tuple)
    pending_id_tuple = tuple(batch.batch_id for batch in pending_batch_tuple)
    scheduled_id_tuple = (*result_id_tuple, *pending_id_tuple)
    if (
        len(batch_id_to_batch) != len(run_input.batch_tuple)
        or set(scheduled_id_tuple) != set(batch_id_to_batch)
        or any(batch_id_to_batch.get(batch.batch_id) != batch for batch in pending_batch_tuple)
    ):
        raise ValueError("Handoff must account for every input exactly once with unchanged pending inputs")
    ledger = current_run_ledger()
    evidence = project_evidence(
        ledger.entries,
        EvidenceQuery(event_kind_tuple=("dispatch_outcome", "natural_execution_outcome", "capability_invocation")),
        run_id=ledger.run_id,
        bounds=EvidenceBounds(),
    )
    return WorkflowHandoff(
        task_id=task_id,
        originating_run_id=ledger.run_id,
        objective=objective,
        criterion=run_input.criterion,
        configuration_label=run_input.configuration.label,
        completed_finding_tuple=tuple(result for result in report.result_tuple if isinstance(result, BatchFinding)),
        pending_batch_tuple=pending_batch_tuple,
        failed_batch_tuple=tuple((batch_id_to_batch[result.batch_id], result) for result in report.result_tuple if isinstance(result, BatchFailure)),
        lesson_tuple=run_input.lesson_tuple,
        selected_evidence_tuple=evidence.evidence_tuple,
    )


async def run_finite_batch(
    run_input: WorkflowInput,
    *,
    maximum_batch_count: int,
    task_id: str,
    objective: str,
    catalog: ProcedureCatalog,
    selection: ProcedureSelection,
    runner: Runner,
    run_entry_grant: Grant,
    handle: RunHandle | None = None,
) -> WorkflowHandoff:
    if maximum_batch_count <= 0:
        raise ValueError("maximum_batch_count must be positive")

    async def body(value: WorkflowInput) -> WorkflowHandoff:
        selected_input = value.model_copy(update={"batch_tuple": value.batch_tuple[:maximum_batch_count]})
        report = await analyze_batches(selected_input, catalog=catalog, selection=selection)
        return make_handoff(value, report, task_id=task_id, objective=objective, pending_batch_tuple=value.batch_tuple[maximum_batch_count:])

    composition = AsyncScaffoldComposition(
        run_contract=RunContract(input_type=WorkflowInput, result_type=WorkflowHandoff),
        body=body,
        run_entry_grant=run_entry_grant,
        composition_perimeter_policy=PerimeterPolicy.NONE,
        function_definition_tuple=(propose_analysis,),
    )
    return await runner.run(composition, run_input, handle=handle)


async def continue_workflow(
    handoff: WorkflowHandoff,
    *,
    runner: Runner,
    run_entry_grant: Grant,
    handle: RunHandle | None = None,
) -> BatchReport:
    """Start a fresh run for pending work only; preserve prior successes and failures.

    Loading never retries failed work. The caller must explicitly reschedule a
    failure for a new run when that is desired. No callable authority is restored.
    """
    pending = await run_async_object_workflow(
        WorkflowInput(
            batch_tuple=handoff.pending_batch_tuple,
            criterion=handoff.criterion,
            lesson_tuple=handoff.lesson_tuple,
            configuration=AnalysisConfiguration(label=handoff.configuration_label),
        ),
        catalog=ProcedureCatalog(),
        selection=ProcedureSelection(procedure_id=None, reason="New run requires fresh procedures"),
        run_entry_grant=run_entry_grant,
        handle=handle,
        runner=runner,
    )
    return BatchReport(
        result_tuple=(*handoff.completed_finding_tuple, *(failure for _, failure in handoff.failed_batch_tuple), *pending.result_tuple)
    )
