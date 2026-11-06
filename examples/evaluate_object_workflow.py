"""Application evaluation against explicit Python rules and identified execution evidence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from examples.async_object_workflow import BatchFinding, BatchReport, SampleBatch, WorkflowInput, run_async_object_workflow
from nighthawk.harness.evidence import EvidenceReference, analyze_run, resolve_evidence
from nighthawk.harness.grants import Grant
from nighthawk.harness.ledger import DispatchEvent, DispatchOutcomeEvent, NaturalExecutionOutcomeEvent, RunEndEvent, RunLedger
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureSelection


@dataclass(frozen=True)
class EvidenceAssessment:
    batch_id: str
    reference: EvidenceReference
    status: Literal["supported", "unavailable", "unrelated"]


@dataclass(frozen=True)
class WorkflowEvaluation:
    """Correctness concerns the declared numeric rule; evidence is assessed separately.

    Citation coverage counts expected findings with citations. Evidence coverage requires
    an exact successful invocation with matching validated inputs and committed output.
    Neither establishes correctness for unobserved inputs or approval for future reuse.
    """

    correct: bool
    citation_coverage: float
    evidence_coverage: float | None
    unresolved_batch_id_tuple: tuple[str, ...]
    evidence_assessment_tuple: tuple[EvidenceAssessment, ...]
    execution_complete: bool | None
    execution_succeeded: bool | None
    effect_observations_complete: bool | None
    model_request_count: int | None
    observed_token_count: int | None
    usage_basis: Literal["provider_responses", "executor_reports", "unavailable"]
    recovered_failure_count: int | None


def fixed_tasks() -> tuple[WorkflowInput, ...]:
    return tuple(
        WorkflowInput(batch_tuple=(SampleBatch(batch_id=name, sample_tuple=values),))
        for name, values in (("boundary", (-2, 0, 3)), ("empty", ()), ("mixed", (8, -1, 0, -4)))
    )


def python_workflow(run_input: WorkflowInput) -> BatchReport:
    """The explicit rule needs no model or generated source for these known requirements."""
    return BatchReport(
        result_tuple=tuple(
            BatchFinding(
                batch_id=batch.batch_id,
                selected_index_value_tuple=tuple((index, value) for index, value in enumerate(batch.sample_tuple) if value < run_input.criterion),
            )
            for batch in run_input.batch_tuple
        )
    )


def _supports_finding(ledger: RunLedger, reference: EvidenceReference, finding: BatchFinding, batch: SampleBatch, criterion: int) -> bool:
    event = resolve_evidence(ledger, reference)
    if not isinstance(event, DispatchOutcomeEvent) or event.status != "success" or event.result != finding.selected_index_value_tuple:
        return False
    return any(
        isinstance(entry.event, DispatchEvent)
        and entry.event.dispatch_node_id == event.dispatch_node_id
        and entry.event.dispatch_kind == "invocation"
        and entry.event.definition_id is not None
        and entry.event.argument_snapshot == {"positional": (batch.sample_tuple, criterion), "keyword": {}}
        for entry in ledger.entries
    )


def evaluate_workflow(
    run_input: WorkflowInput,
    report: BatchReport,
    *,
    ledger_tuple: tuple[RunLedger, ...] = (),
    previously_failed_batch_id_tuple: tuple[str, ...] | None = None,
) -> WorkflowEvaluation:
    """Compare every expected input and every reported result without hiding duplicates.

    Checks are application-level assessment of reversible results, not authority grants.
    Callers explicitly choose ledgers, including earlier runs after a continuation.
    References are checked against those ledgers; unknown or unrelated evidence is reported.
    Usage records are observations, not monetary cost or evidence of improved model quality.
    """
    # Recheck the input contract even for values produced with unchecked model_copy.
    identities = tuple(batch.batch_id for batch in run_input.batch_tuple)
    if len(set(identities)) != len(identities):
        raise ValueError("Evaluation requires unique batch identities")
    batch_id_to_batch = {batch.batch_id: batch for batch in run_input.batch_tuple}
    batch_id_to_expected = {
        finding.batch_id: finding.selected_index_value_tuple
        for finding in python_workflow(run_input).result_tuple
        if isinstance(finding, BatchFinding)
    }
    batch_id_to_count = Counter(result.batch_id for result in report.result_tuple)
    batch_id_to_finding = {
        result.batch_id: result for result in report.result_tuple if isinstance(result, BatchFinding) and batch_id_to_count[result.batch_id] == 1
    }
    resolved = {
        batch_id
        for batch_id, expected in batch_id_to_expected.items()
        if batch_id in batch_id_to_finding and batch_id_to_finding[batch_id].selected_index_value_tuple == expected
    }
    unresolved = tuple(batch_id for batch_id in identities if batch_id not in resolved)
    correct = not unresolved and len(report.result_tuple) == len(identities) and set(batch_id_to_count) == set(identities)
    run_id_to_ledger = {ledger.run_id: ledger for ledger in ledger_tuple}
    if len(run_id_to_ledger) != len(ledger_tuple):
        raise ValueError("Evaluation requires unique run identities")
    evidence_assessments: list[EvidenceAssessment] = []
    covered: set[str] = set()
    cited: set[str] = set()
    for batch_id, finding in batch_id_to_finding.items():
        if batch_id not in batch_id_to_batch:
            evidence_assessments.extend(EvidenceAssessment(batch_id, reference, "unrelated") for reference in finding.evidence_reference_tuple)
            continue
        if finding.evidence_reference_tuple:
            cited.add(batch_id)
        for reference in finding.evidence_reference_tuple:
            ledger = run_id_to_ledger.get(reference.run_id)
            if ledger is None:
                status = "unavailable"
            else:
                try:
                    status = (
                        "supported"
                        if _supports_finding(ledger, reference, finding, batch_id_to_batch[batch_id], run_input.criterion)
                        else "unrelated"
                    )
                except ValueError:
                    status = "unavailable"
            evidence_assessments.append(EvidenceAssessment(batch_id, reference, status))
            if status == "supported":
                covered.add(batch_id)
    analyses = tuple(analyze_run(ledger) for ledger in ledger_tuple)
    terminal_event_tuple = tuple(tuple(entry.event for entry in ledger.entries if isinstance(entry.event, RunEndEvent)) for ledger in ledger_tuple)
    all_terminal = bool(ledger_tuple) and all(len(events) == 1 for events in terminal_event_tuple)
    outcomes = tuple(entry.event for ledger in ledger_tuple for entry in ledger.entries if isinstance(entry.event, NaturalExecutionOutcomeEvent))
    provider_observed = any(event.response_model_name_tuple for event in outcomes) and all(
        len(event.response_model_name_tuple) == event.executor_usage.request_count
        and all(name is not None for name in event.response_model_name_tuple)
        for event in outcomes
    )
    recovered = None
    if previously_failed_batch_id_tuple is not None:
        if len(set(previously_failed_batch_id_tuple)) != len(previously_failed_batch_id_tuple) or not set(previously_failed_batch_id_tuple) <= set(
            identities
        ):
            raise ValueError("Recovery history requires unique expected batch identities")
        recovered = len(set(previously_failed_batch_id_tuple) & resolved)
    return WorkflowEvaluation(
        correct=correct,
        citation_coverage=len(cited) / len(identities) if identities else 1.0,
        evidence_coverage=(len(covered) / len(identities) if identities else 1.0) if ledger_tuple else None,
        unresolved_batch_id_tuple=unresolved,
        evidence_assessment_tuple=tuple(evidence_assessments),
        execution_complete=(all_terminal and all(not analysis.active_node_id_tuple for analysis in analyses)) if ledger_tuple else None,
        execution_succeeded=all(events[0].terminal_kind == "accepted" for events in terminal_event_tuple) if all_terminal else None,
        # This is the live ledger's observation, not certification of durable storage.
        effect_observations_complete=all(not analysis.unmatched_effect_attempt_tuple for analysis in analyses) if ledger_tuple else None,
        model_request_count=sum(node.own_usage.request_count for analysis in analyses for node in analysis.node_tuple) if ledger_tuple else None,
        observed_token_count=sum(node.own_usage.total_token_count for analysis in analyses for node in analysis.node_tuple) if ledger_tuple else None,
        usage_basis=("provider_responses" if provider_observed else "executor_reports") if ledger_tuple else "unavailable",
        recovered_failure_count=recovered,
    )


@dataclass(frozen=True)
class WorkflowComparison:
    run_input: WorkflowInput
    python_elapsed_seconds: float
    generated_elapsed_seconds: float
    evaluation: WorkflowEvaluation


async def evaluate_fixed_tasks(*, runner: Runner) -> tuple[WorkflowComparison, ...]:
    """Measure the same inputs against Python and generated composition.

    The caller selects the executor and allocation. Scripted runs test composition;
    provider runs measure only these tasks, this configuration, and this sample count.
    The output makes no general claim that generation is preferable to Python.
    """
    comparisons: list[WorkflowComparison] = []
    for run_input in fixed_tasks():
        started = perf_counter()
        python_workflow(run_input)
        python_elapsed = perf_counter() - started
        handle = RunHandle()
        started = perf_counter()
        report = await run_async_object_workflow(
            run_input,
            runner=runner,
            run_entry_grant=Grant.EMPTY,
            handle=handle,
            catalog=ProcedureCatalog(),
            selection=ProcedureSelection(procedure_id=None, reason="Evaluate a fresh composition"),
        )
        generated_elapsed = perf_counter() - started
        comparisons.append(
            WorkflowComparison(run_input, python_elapsed, generated_elapsed, evaluate_workflow(run_input, report, ledger_tuple=(handle.ledger,)))
        )
    return tuple(comparisons)
