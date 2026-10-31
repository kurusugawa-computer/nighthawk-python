from __future__ import annotations

from collections.abc import Iterable, Mapping

import nighthawk as nh
from nighthawk.runtime import StepEnvironment


def new_step_context(
    *,
    execution_reference: nh.ExecutionReference | None = None,
    step_globals: dict[str, object] | None = None,
    step_locals: dict[str, object] | None = None,
    output_binding_names: Iterable[str] = (),
    read_binding_names: frozenset[str] = frozenset(),
    task_name_to_value: Mapping[str, object] | None = None,
    binding_name_to_type: Mapping[str, object] | None = None,
    type_alias_reference_names: frozenset[str] = frozenset(),
) -> nh.StepContext:
    step_globals = {} if step_globals is None else step_globals
    step_locals = {} if step_locals is None else step_locals
    return nh.StepContext(
        execution_reference=execution_reference or nh.ExecutionReference("run", "scope", "step", "test"),
        environment=StepEnvironment(
            python_name_to_value=step_locals,
            python_globals=step_globals,
            task_name_to_value=task_name_to_value or {},
            output_name_set=frozenset(output_binding_names),
            binding_name_to_type=binding_name_to_type or {},
        ),
        step_globals=step_globals,
        step_locals=step_locals,
        read_binding_names=read_binding_names,
        type_alias_reference_names=type_alias_reference_names,
    )


def new_context(**name_to_value: object) -> nh.StepContext:
    return new_step_context(step_locals=name_to_value)
