from __future__ import annotations

import nighthawk as nh


def new_context(**name_to_value: object) -> nh.StepContext:
    return nh.StepContext(
        execution_reference=nh.ExecutionReference("run", "scope", "step", "test"),
        step_globals={},
        step_locals=name_to_value,
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        task_name_to_value={},
    )
