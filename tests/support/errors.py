from __future__ import annotations

import nighthawk as nh
from nighthawk.lifecycle import StepFailed


def execution_error(message: str) -> nh.ExecutionError:
    return nh.ExecutionError(
        StepFailed(
            execution_reference=nh.ExecutionReference("run", "scope", "step", "test"),
            failure_stage="executor",
            original_exception=RuntimeError(message),
        )
    )
