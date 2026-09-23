from __future__ import annotations

import pytest

import nighthawk as nh
from nighthawk.runtime import StepCandidate, StepRequest
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.model_candidate import resolve_model_candidate
from nighthawk.runtime.step_contract import RaiseStepOutcome


@pytest.mark.parametrize("error_type", ["MissingException", "invalid_exception"])
@pytest.mark.parametrize("decision", [nh.oversight.Accept(), nh.oversight.Rewrite(outcome=nh.oversight.Pass())])
def test_invalid_raise_type_fails_before_inspection(error_type: str, decision: nh.oversight.StepCommitDecision) -> None:
    inspected: list[nh.oversight.StepCommit] = []
    invalid_exception = int  # noqa: F841

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return run_coroutine_synchronously(
                lambda: resolve_model_candidate(request, RaiseStepOutcome(kind="raise", raise_message="failed", raise_error_type=error_type), {})
            )

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.StepCommitDecision:
        inspected.append(commit)
        return decision

    @nh.natural_function
    def workflow() -> int:
        nonlocal invalid_exception
        """natural
        <invalid_exception>
        Raise an error.
        """
        return 7

    with (
        nh.run(Executor()),
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=inspect)),
        pytest.raises(nh.ExecutionError, match="Invalid raise_error_type"),
    ):
        workflow()
    assert inspected == []
