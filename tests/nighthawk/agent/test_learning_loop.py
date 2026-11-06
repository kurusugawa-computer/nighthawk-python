"""The loop records a controlled failure, derives a lesson, and supplies it later."""

from dataclasses import replace

from examples.learning_loop import LearningLoopInput, build_learning_validation, run_learning_loop
from nighthawk.agent import CapabilityInvocationEvent, CodeProposal, RunHandle
from nighthawk.agent.history import HistoryProjection
from nighthawk.agent.learning import LearningResult
from nighthawk.runtime.step_request import StepCandidate, StepRequest
from nighthawk.testing import CallbackExecutor

from .conftest import make_runner
from .test_learning import lesson


def learning_handler(call: StepRequest) -> StepCandidate:
    if "result" in call.output_binding_names:
        evidence = call.step_context.step_locals["evidence"]
        assert isinstance(evidence, HistoryProjection)
        assert '"matches_criterion":false' in evidence.evidence_tuple[0].excerpt
        return StepCandidate(binding_name_to_value={"result": LearningResult(lesson_tuple=(lesson(evidence.evidence_tuple[0].reference),))})
    assert call.step_context.step_locals["lessons"]
    return StepCandidate(
        binding_name_to_value={
            "proposal": CodeProposal(
                source="def find_negatives(samples: tuple[int, ...]) -> tuple[int, ...]:\n    return tuple(value for value in samples if value < 0)\n",
                export_name_tuple=("find_negatives",),
            )
        }
    )


def test_explicit_learning_loop_corrects_controlled_boundary_failure() -> None:
    handle = RunHandle()
    result = run_learning_loop(
        LearningLoopInput(),
        run_entry_grant=build_learning_validation(),
        handle=handle,
        runner=replace(make_runner(), step_executor=CallbackExecutor(learning_handler)),
    )
    first, second = result.trial_tuple
    assert first.selected_value_tuple == (-2, 0) and not first.matches_criterion
    assert first.expected_value_tuple == (-2,)
    assert second.selected_value_tuple == (-2,) and second.matches_criterion
    assert first.lesson_input_tuple == ()
    assert second.lesson_input_tuple == result.learning.lesson_tuple
    outcomes = [entry.event for entry in handle.ledger.entries if isinstance(entry.event, CapabilityInvocationEvent)]
    assert len(outcomes) == 2
    assert outcomes[0].result["lesson_input_tuple"] == ()  # type: ignore[index]
    assert len(outcomes[1].result["lesson_input_tuple"]) == len(result.learning.lesson_tuple)  # type: ignore[index]
