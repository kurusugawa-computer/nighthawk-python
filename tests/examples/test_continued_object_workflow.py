import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from examples.async_object_workflow import BatchFailure, BatchFinding, WorkflowInput
from examples.continued_object_workflow import WorkflowHandoff, continue_workflow, decode_workflow_handoff, encode_workflow_handoff, run_finite_batch
from nighthawk.adapters.artifacts import DirectoryArtifactStore
from nighthawk.harness.grants import Grant
from nighthawk.harness.ledger import RunEntryEvent
from nighthawk.harness.run_handle import RunHandle
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureSelection
from tests.examples.workflow_support import analysis_executor, sample_batches
from tests.support.harness import make_runner


def test_save_reopen_and_finish_only_pending_batches_in_fresh_run(tmp_path: Path) -> None:
    initial = RunHandle()
    handoff = asyncio.run(
        run_finite_batch(
            WorkflowInput(batch_tuple=sample_batches()),
            maximum_batch_count=1,
            task_id="task",
            objective="strict negatives",
            catalog=ProcedureCatalog(),
            selection=ProcedureSelection(procedure_id=None, reason="Propose"),
            run_entry_grant=Grant.EMPTY,
            handle=initial,
            runner=replace(make_runner(), step_executor=analysis_executor(1)),
        )
    )
    assert handoff.completed_finding_tuple[0].batch_id == "first"
    assert [batch.batch_id for batch in handoff.pending_batch_tuple] == ["second", "third"]
    store = DirectoryArtifactStore(tmp_path)
    descriptor = store.put_text((encode_workflow_handoff(handoff),), media_type="application/json")
    reopened = DirectoryArtifactStore(tmp_path)
    restored = decode_workflow_handoff(reopened.load_text(descriptor, maximum_character_count=100000), expected_task_id="task")
    assert restored == handoff and restored is not handoff
    assert restored.pending_batch_tuple[0] is not handoff.pending_batch_tuple[0]
    continuation = RunHandle()
    report = asyncio.run(
        continue_workflow(
            restored, run_entry_grant=Grant.EMPTY, handle=continuation, runner=replace(make_runner(), step_executor=analysis_executor(2))
        )
    )
    assert [result.batch_id for result in report.result_tuple] == ["first", "second", "third"]
    assert [result.selected_index_value_tuple for result in report.result_tuple if isinstance(result, BatchFinding)] == [
        ((1, -2),),
        ((0, -3),),
        ((1, -5),),
    ]
    first_entry = next(entry.event for entry in initial.ledger.entries if isinstance(entry.event, RunEntryEvent))
    next_entry = next(entry.event for entry in continuation.ledger.entries if isinstance(entry.event, RunEntryEvent))
    assert first_entry.run_id != next_entry.run_id and first_entry.resource_allocation_identity != next_entry.resource_allocation_identity


def test_failed_batches_are_carried_unchanged_and_never_implicitly_retried() -> None:
    batch = sample_batches()[0]
    failure = BatchFailure(batch_id=batch.batch_id, failure_class_name="ValueError", detail="ordinary failure")
    handoff = WorkflowHandoff(
        task_id="task",
        originating_run_id="earlier",
        objective="strict negatives",
        criterion=0,
        configuration_label="numeric samples",
        failed_batch_tuple=((batch, failure),),
    )
    report = asyncio.run(continue_workflow(handoff, run_entry_grant=Grant.EMPTY, runner=replace(make_runner(), step_executor=analysis_executor(0))))
    assert report.result_tuple == (failure,)


def test_decode_rejects_wrong_task_version_and_overlapping_schedule() -> None:
    handoff = WorkflowHandoff(
        task_id="task",
        originating_run_id="run",
        objective="negative",
        criterion=0,
        configuration_label="numeric samples",
        pending_batch_tuple=sample_batches(),
    )
    encoded = encode_workflow_handoff(handoff)
    with pytest.raises(ValueError, match="task"):
        decode_workflow_handoff(encoded, expected_task_id="other")
    with pytest.raises(ValueError, match="version"):
        decode_workflow_handoff(encoded.replace('"format_version":1', '"format_version":2'), expected_task_id="task")
    with pytest.raises(ValueError, match="disjoint"):
        WorkflowHandoff(
            task_id="task",
            originating_run_id="run",
            objective="negative",
            criterion=0,
            configuration_label="numeric samples",
            pending_batch_tuple=(sample_batches()[0],) * 2,
        )
