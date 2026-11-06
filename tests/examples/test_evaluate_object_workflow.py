import pytest

from examples.async_object_workflow import BatchFailure, BatchFinding, BatchReport, SampleBatch, WorkflowInput
from examples.evaluate_object_workflow import evaluate_workflow


@pytest.mark.parametrize(
    "result_tuple",
    [
        (
            BatchFinding(batch_id="expected", selected_index_value_tuple=((1, 0),)),
            BatchFinding(batch_id="expected", selected_index_value_tuple=((0, -2),)),
        ),
        (
            BatchFinding(batch_id="expected", selected_index_value_tuple=((0, -2),)),
            BatchFinding(batch_id="expected", selected_index_value_tuple=((1, 0),)),
        ),
        (BatchFinding(batch_id="expected", selected_index_value_tuple=((0, -2),)),) * 2,
        (
            BatchFinding(batch_id="expected", selected_index_value_tuple=((0, -2),)),
            BatchFailure(batch_id="unexpected", failure_class_name="ValueError", detail="Ordinary failure"),
        ),
        (
            BatchFailure(batch_id="unexpected", failure_class_name="ValueError", detail="Ordinary failure"),
            BatchFinding(batch_id="expected", selected_index_value_tuple=((0, -2),)),
        ),
        (
            BatchFinding(batch_id="expected", selected_index_value_tuple=((0, -2),)),
            BatchFinding(batch_id="unexpected", selected_index_value_tuple=()),
        ),
        (BatchFailure(batch_id="unexpected", failure_class_name="ValueError", detail="Ordinary failure"),),
        (),
    ],
    ids=[
        "wrong-then-correct",
        "correct-then-wrong",
        "identical-duplicates",
        "extra-failure",
        "extra-failure-first",
        "extra-finding",
        "wrong-batch",
        "missing",
    ],
)
def test_incorrect_report_cannot_hide_results(result_tuple: tuple[BatchFinding | BatchFailure, ...]) -> None:
    run_input = WorkflowInput(batch_tuple=(SampleBatch(batch_id="expected", sample_tuple=(-2, 0, 3)),))

    evaluation = evaluate_workflow(run_input, BatchReport(result_tuple=result_tuple))

    assert not evaluation.correct


def test_complete_report_can_arrive_in_a_different_batch_order() -> None:
    run_input = WorkflowInput(batch_tuple=(SampleBatch(batch_id="first", sample_tuple=(-2, 0, 3)), SampleBatch(batch_id="second", sample_tuple=())))
    report = BatchReport(
        result_tuple=(
            BatchFinding(batch_id="second", selected_index_value_tuple=()),
            BatchFinding(batch_id="first", selected_index_value_tuple=((0, -2),)),
        )
    )

    assert evaluate_workflow(run_input, report).correct


def test_empty_task_accepts_only_an_empty_report() -> None:
    run_input = WorkflowInput(batch_tuple=())

    assert evaluate_workflow(run_input, BatchReport(result_tuple=())).correct
    report = BatchReport(result_tuple=(BatchFailure(batch_id="unexpected", failure_class_name="ValueError", detail="Ordinary failure"),))
    assert not evaluate_workflow(run_input, report).correct
