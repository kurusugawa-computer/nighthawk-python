"""Concurrent numeric analysis using retained objects and existing async dispatch.

TaskGroup joins cooperative branches. It does not establish forced cessation of
uncooperative code; that remains the platform's obligation. Ordinary analysis
errors are explicit domain results, while host control errors propagate.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field, model_validator

import nighthawk as nh
from nighthawk.agent import (
    AsyncGeneratedCallableReference,
    CodeProposal,
    DispatchEvent,
    DispatchOutcomeEvent,
    Grant,
    PerimeterPolicy,
    RunContract,
    RunHandle,
    current_grant,
    current_run_ledger,
    scope,
)
from nighthawk.agent.catalog import ProcedureCatalog, ProcedureSelection
from nighthawk.agent.history import EvidenceReference
from nighthawk.agent.learning import Lesson
from nighthawk.agent.procedures import materialize_procedure, selected_procedure
from nighthawk.agent.runtime import AsyncScaffoldComposition, Runner


class SampleBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    batch_id: str
    sample_tuple: tuple[int, ...]


class AnalysisConfiguration(BaseModel):
    """Deliberately shareable ordinary configuration; mutation has alias semantics."""

    label: str = "numeric samples"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    batch: SampleBatch
    criterion: int = 0
    lesson_tuple: tuple[Lesson, ...] = ()
    configuration: AnalysisConfiguration


class BatchFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    batch_id: str
    selected_index_value_tuple: tuple[tuple[int, int], ...]
    evidence_reference_tuple: tuple[EvidenceReference, ...] = ()


class BatchFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    batch_id: str
    failure_class_name: str
    detail: str = Field(max_length=2000)
    evidence_reference_tuple: tuple[EvidenceReference, ...] = ()


class BatchReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    result_tuple: tuple[BatchFinding | BatchFailure, ...]


class WorkflowInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    batch_tuple: tuple[SampleBatch, ...]
    criterion: int = 0
    lesson_tuple: tuple[Lesson, ...] = ()
    configuration: AnalysisConfiguration = Field(default_factory=AnalysisConfiguration)

    @model_validator(mode="after")
    def _unique_batch_identities(self) -> WorkflowInput:
        identities = tuple(batch.batch_id for batch in self.batch_tuple)
        if len(identities) != len(set(identities)):
            raise ValueError("Batch identities must be unique")
        return self


@nh.function
async def propose_analysis(request: AnalysisRequest) -> CodeProposal:
    proposal: CodeProposal | None = None
    """execute
    ---
    deny: [raise, return, break, continue]
    ---
    Read <request>, including its explicitly supplied lessons and strict upper-bound criterion.
    Set <:proposal> to <CodeProposal>(source=..., export_name_tuple=("analyze",)).
    Define only async def analyze(samples: tuple[int, ...], criterion: int) -> tuple[tuple[int, int], ...].
    Return every (original index, value) whose value < criterion, in original order.
    Input samples remain Python objects; use ordinary enumeration, not a handwritten list.
    The host will materialize your inert definition and await its mediated reference.
    """
    if proposal is None:
        raise ValueError("A sample analysis proposal is required")
    return proposal


async def analyze_batch(request: AnalysisRequest, *, catalog: ProcedureCatalog, selection: ProcedureSelection) -> BatchFinding | BatchFailure:
    if selection.procedure_id is None:
        with scope(current_grant().restrict(())):
            proposal = await propose_analysis(request)
            selection = materialize_procedure(catalog, proposal, export_name="analyze", purpose="Select indexed samples below a strict upper bound")
    with selected_procedure(catalog, selection) as procedure:
        if not isinstance(procedure, AsyncGeneratedCallableReference):
            raise TypeError("The sample example requires an async generated procedure")
        ledger = current_run_ledger()
        starting_sequence = len(ledger.entries)
        try:
            selected = await procedure(request.batch.sample_tuple, request.criterion)
        except (ArithmeticError, ValueError) as failure:
            # Convert only ordinary failures of the generated analysis invocation.
            # Neighboring concurrent events cannot establish this branch's evidence.
            return BatchFailure(batch_id=request.batch.batch_id, failure_class_name=type(failure).__name__, detail=str(failure)[:2000])
    # Match exact validated inputs, definition identity, and committed result.
    # Ambiguous concurrent identical invocations remain uncited rather than guessed.
    entries = ledger.entries[starting_sequence:]
    invocation_node_id_set = {
        entry.event.dispatch_node_id
        for entry in entries
        if isinstance(entry.event, DispatchEvent)
        and entry.event.dispatch_kind == "invocation"
        and entry.event.definition_id == procedure.definition.definition_id
        and entry.event.argument_snapshot == {"positional": (request.batch.sample_tuple, request.criterion), "keyword": {}}
    }
    outcomes = tuple(
        entry
        for entry in entries
        if isinstance(entry.event, DispatchOutcomeEvent)
        and entry.event.dispatch_node_id in invocation_node_id_set
        and entry.event.status == "success"
        and entry.event.result == selected
    )
    references = (EvidenceReference(run_id=ledger.run_id, event_id=outcomes[0].event_id),) if len(outcomes) == 1 else ()
    return BatchFinding.model_validate(
        {"batch_id": request.batch.batch_id, "selected_index_value_tuple": selected, "evidence_reference_tuple": references}
    )


async def analyze_batches(run_input: WorkflowInput, *, catalog: ProcedureCatalog, selection: ProcedureSelection) -> BatchReport:
    async with asyncio.TaskGroup() as group:
        task_list = [
            group.create_task(
                analyze_batch(
                    AnalysisRequest(
                        batch=batch, criterion=run_input.criterion, lesson_tuple=run_input.lesson_tuple, configuration=run_input.configuration
                    ),
                    catalog=catalog,
                    selection=selection,
                )
            )
            for batch in run_input.batch_tuple
        ]
    return BatchReport(result_tuple=tuple(task.result() for task in task_list))


def build_async_object_composition(
    *, catalog: ProcedureCatalog, selection: ProcedureSelection, run_entry_grant: Grant
) -> AsyncScaffoldComposition[WorkflowInput, BatchReport]:
    async def body(run_input: WorkflowInput) -> BatchReport:
        return await analyze_batches(run_input, catalog=catalog, selection=selection)

    return AsyncScaffoldComposition(
        run_contract=RunContract(input_type=WorkflowInput, result_type=BatchReport),
        body=body,
        run_entry_grant=run_entry_grant,
        composition_perimeter_policy=PerimeterPolicy.NONE,
        function_definition_tuple=(propose_analysis,),
    )


async def run_async_object_workflow(
    run_input: WorkflowInput,
    *,
    catalog: ProcedureCatalog,
    selection: ProcedureSelection,
    runner: Runner,
    run_entry_grant: Grant,
    handle: RunHandle | None = None,
) -> BatchReport:
    return await runner.run(
        build_async_object_composition(catalog=catalog, selection=selection, run_entry_grant=run_entry_grant), run_input, handle=handle
    )
