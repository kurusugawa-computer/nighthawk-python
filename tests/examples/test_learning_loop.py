from dataclasses import replace

from examples.learning_loop import LearningLoopInput, build_learning_validation, run_learning_loop
from nighthawk.core.testing import CallbackExecutor
from nighthawk.harness.ledger import CapabilityInvocationEvent
from nighthawk.harness.run_handle import RunHandle
from tests.examples.workflow_support import learning_handler
from tests.support.harness import make_runner


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
