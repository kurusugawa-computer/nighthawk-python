from dataclasses import replace

import pytest
from pydantic import ValidationError

from examples.async_object_workflow import BatchFinding, BatchReport, SampleBatch, WorkflowInput, run_async_object_workflow
from examples.continued_object_workflow import continue_workflow, run_finite_batch
from examples.evaluate_object_workflow import evaluate_fixed_tasks, evaluate_workflow
from nighthawk.harness.context import current_run_ledger
from nighthawk.harness.evidence import EvidenceReference, resolve_evidence
from nighthawk.harness.grants import Grant
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.run_handle import RunHandle
from nighthawk.parts.procedures import materialize_procedure, selected_procedure
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureSelection
from tests.examples.workflow_support import analysis_executor
from tests.support.harness import make_runner
from tests.support.runs import EchoInput, EchoResult, run_body


def test_materialization_selects_a_declared_callable_without_invoking_it() -> None:
    proposal = CodeProposal(
        source="def selected(value: int) -> int:\n    return value + 1\ndef unused() -> int:\n    raise ValueError('must not run')\n",
        export_name_tuple=("selected", "unused"),
    )
    catalog = ProcedureCatalog()

    def body(value: EchoInput) -> EchoResult:
        selection = materialize_procedure(catalog, proposal, export_name="selected", purpose="Increment a value")
        assert len(catalog.procedure_id_to_value) == 1
        with selected_procedure(catalog, selection) as function:
            assert callable(function) and function(6) == 7
        before = len(current_run_ledger().entries)
        with pytest.raises(ValueError, match="definition-only"):
            materialize_procedure(
                catalog,
                proposal.model_copy(update={"execution_intent": "definition_and_invocation", "entry_export_name": "unused"}),
                export_name="unused",
                purpose="Refused",
            )
        assert len(current_run_ledger().entries) == before
        return EchoResult()

    run_body(body, runner=make_runner())


async def test_async_workflow_records_exact_input_and_output_evidence_on_callers_loop() -> None:
    handle = RunHandle()
    run_input = WorkflowInput(batch_tuple=(SampleBatch(batch_id="expected", sample_tuple=(-2, 0, 3)),))
    report = await run_async_object_workflow(
        run_input,
        catalog=ProcedureCatalog(),
        selection=ProcedureSelection(procedure_id=None, reason="Construct"),
        runner=replace(make_runner(), step_executor=analysis_executor(1)),
        run_entry_grant=Grant.EMPTY,
        handle=handle,
    )
    evaluation = evaluate_workflow(run_input, report, ledger_tuple=(handle.ledger,))
    assert evaluation.correct and evaluation.execution_complete
    assert evaluation.citation_coverage == evaluation.evidence_coverage == 1.0
    assert {assessment.status for assessment in evaluation.evidence_assessment_tuple} == {"supported"}
    assert evaluation.usage_basis == "executor_reports"
    finding = report.result_tuple[0]
    reference = finding.evidence_reference_tuple[0]
    assert resolve_evidence(handle.ledger, reference.model_copy(update={"value_path_tuple": ("result", 0, 1)})) == -2
    with pytest.raises(ValueError):
        resolve_evidence(handle.ledger, reference.model_copy(update={"run_id": "different"}))
    with pytest.raises(ValueError):
        resolve_evidence(handle.ledger, reference.model_copy(update={"value_path_tuple": ("result", 999)}))
    wrong_input = WorkflowInput(batch_tuple=(SampleBatch(batch_id="expected", sample_tuple=(-2, 7, 8)),))
    # Same expected answer cannot make an observation of different input into support.
    assert evaluate_workflow(wrong_input, report, ledger_tuple=(handle.ledger,)).evidence_coverage == 0.0


async def test_saved_continuation_preserves_nondefault_criterion_and_evidence() -> None:
    run_input = WorkflowInput(
        batch_tuple=(SampleBatch(batch_id="first", sample_tuple=(1, 3, 6)), SampleBatch(batch_id="second", sample_tuple=(2, 5))), criterion=4
    )
    first, second = RunHandle(), RunHandle()
    runner = replace(make_runner(), step_executor=analysis_executor(1))
    handoff = await run_finite_batch(
        run_input,
        maximum_batch_count=1,
        task_id="criterion",
        objective="below four",
        catalog=ProcedureCatalog(),
        selection=ProcedureSelection(procedure_id=None, reason="Construct"),
        runner=runner,
        run_entry_grant=Grant.EMPTY,
        handle=first,
    )
    assert handoff.criterion == 4
    report = await continue_workflow(
        handoff, runner=replace(make_runner(), step_executor=analysis_executor(1)), run_entry_grant=Grant.EMPTY, handle=second
    )
    assert first.run_id != second.run_id
    evaluation = evaluate_workflow(run_input, report, ledger_tuple=(first.ledger, second.ledger))
    assert evaluation.correct and evaluation.evidence_coverage == 1.0
    assert evaluation.execution_complete


def test_duplicate_inputs_and_fabricated_citations_cannot_inflate_evaluation() -> None:
    batch = SampleBatch(batch_id="expected", sample_tuple=(-2,))
    with pytest.raises(ValidationError, match="unique"):
        WorkflowInput(batch_tuple=(batch, batch))
    run_input = WorkflowInput(batch_tuple=(batch,))
    with pytest.raises(ValueError, match="unique"):
        evaluate_workflow(run_input.model_copy(update={"batch_tuple": (batch, batch)}), BatchReport(result_tuple=()))
    reference = EvidenceReference(run_id="invented", event_id="invented")
    report = BatchReport(
        result_tuple=(
            BatchFinding(batch_id="expected", selected_index_value_tuple=((0, -2),), evidence_reference_tuple=(reference,)),
            BatchFinding(batch_id="unexpected", selected_index_value_tuple=(), evidence_reference_tuple=(reference,)),
        )
    )
    evaluation = evaluate_workflow(run_input, report)
    assert not evaluation.correct
    assert evaluation.citation_coverage == 1.0
    assert evaluation.evidence_coverage is None
    assert [assessment.status for assessment in evaluation.evidence_assessment_tuple] == ["unavailable", "unrelated"]
    assert evaluation.execution_complete is None
    assert evaluation.model_request_count is None


async def test_fixed_tasks_compare_python_and_generated_behavior_without_quality_claims() -> None:
    comparisons = await evaluate_fixed_tasks(runner=replace(make_runner(), step_executor=analysis_executor(3)))
    assert len(comparisons) == 3
    for comparison in comparisons:
        assert comparison.evaluation.correct
        assert comparison.evaluation.evidence_coverage == 1.0
        assert comparison.evaluation.usage_basis == "executor_reports"
        assert comparison.python_elapsed_seconds >= 0
        assert comparison.generated_elapsed_seconds >= 0


def test_invocation_evidence_captures_inputs_before_mutation() -> None:
    from nighthawk.harness.dispatch import dispatch
    from nighthawk.harness.ledger import DispatchEvent

    values = [1]
    handle = RunHandle()

    def body(value: EchoInput) -> EchoResult:
        procedure = dispatch(
            CodeProposal(source="def append(values: object) -> list[int]:\n    values.append(2)\n    return values\n", export_name_tuple=("append",))
        ).export_name_to_value["append"]
        assert callable(procedure)
        assert procedure(values) == [1, 2]
        values.append(3)
        return EchoResult()

    run_body(body, runner=make_runner(), handle=handle)
    invocation = next(
        entry.event
        for entry in handle.ledger.select(DispatchEvent)
        if isinstance(entry.event, DispatchEvent) and entry.event.dispatch_kind == "invocation"
    )
    assert invocation.argument_snapshot == {"positional": ((1,),), "keyword": {}}
    assert values == [1, 2, 3]


@pytest.mark.parametrize("pending", [(), (SampleBatch(batch_id="expected", sample_tuple=(999,)),)])
def test_handoff_cannot_drop_work_or_change_pending_input(pending: tuple[SampleBatch, ...]) -> None:
    from examples.continued_object_workflow import make_handoff

    run_input = WorkflowInput(batch_tuple=(SampleBatch(batch_id="expected", sample_tuple=(1, 2)),))
    with pytest.raises(ValueError, match="every input exactly once"):
        make_handoff(run_input, BatchReport(result_tuple=()), task_id="task", objective="filter", pending_batch_tuple=pending)


def test_duplicate_findings_do_not_count_as_recovered_work() -> None:
    run_input = WorkflowInput(batch_tuple=(SampleBatch(batch_id="expected", sample_tuple=(-2,)),))
    finding = BatchFinding(batch_id="expected", selected_index_value_tuple=((0, -2),))
    report = BatchReport(result_tuple=(finding, finding))
    evaluation = evaluate_workflow(run_input, report, previously_failed_batch_id_tuple=("expected",))
    assert not evaluation.correct
    assert evaluation.unresolved_batch_id_tuple == ("expected",)
    assert evaluation.recovered_failure_count == 0
    with pytest.raises(ValueError, match="unique"):
        evaluate_workflow(run_input, report, previously_failed_batch_id_tuple=("expected", "expected"))


def test_failed_but_ended_execution_is_distinct_from_success() -> None:
    from nighthawk.harness.errors import RunFailedError

    handle = RunHandle()

    def body(value: EchoInput) -> EchoResult:
        raise ValueError("observed failure")

    with pytest.raises(RunFailedError):
        run_body(body, runner=make_runner(), handle=handle)
    evaluation = evaluate_workflow(WorkflowInput(batch_tuple=()), BatchReport(result_tuple=()), ledger_tuple=(handle.ledger,))
    assert evaluation.correct  # The empty domain report is correct independently of execution failure.
    assert evaluation.execution_complete is True
    assert evaluation.execution_succeeded is False
    assert evaluation.effect_observations_complete is True
