"""Tracked output and temporary writes, plus validated Pydantic field assignment."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import CodeType
from typing import Literal, cast

from pydantic import BaseModel, TypeAdapter, ValidationError
from typing_extensions import TypeForm

from ..identifier_path import parse_identifier_path
from .async_bridge import run_coroutine_synchronously
from .expression import compile_expression, evaluate_compiled_expression
from .operation_contract import OperationError, OperationPhase
from .operations import OperationExecution, execute_operation_async, new_operation_request
from .step_context import StepContext


@dataclass(frozen=True)
class AssignmentResult:
    target_path: str
    value: object
    retained_as: Literal["temporary", "output", "attribute"]


def _failure(message: str, *, phase: OperationPhase, evaluation_started: bool) -> OperationError:
    return OperationError(
        message=message,
        phase=phase,
        evaluation_started=evaluation_started,
        guidance=(
            "Earlier right-hand-side or validator effects may remain. Correct the target or value; use an ordinary Python helper for unsupported mutations."
            if evaluation_started
            else "Evaluation has not started. Fix the target; existing Python names require an output declaration."
        ),
    )


def admit_assignment(step_context: StepContext, target_path: str) -> tuple[str, ...]:
    parsed = parse_identifier_path(target_path)
    if parsed is None:
        raise _failure(
            "Invalid target_path; expected name(.field)* with ASCII identifiers and no __ segments", phase="admission", evaluation_started=False
        )
    try:
        if len(parsed) == 1:
            step_context.environment.check_assignment(parsed[0])
        else:
            step_context.environment.resolve(parsed[0])
    except (ValueError, NameError) as exception:
        raise _failure(str(exception), phase="admission", evaluation_started=False) from exception
    return parsed


def assign_value(
    step_context: StepContext, target_path: str, parsed: tuple[str, ...], value: object, execution: OperationExecution
) -> AssignmentResult:
    name = parsed[0]
    if len(parsed) == 1:
        execution.phase = "validation"
        if name in step_context.binding_name_to_type:
            try:
                value = TypeAdapter(cast(TypeForm[object], step_context.binding_name_to_type[name])).validate_python(value)
            except ValidationError as exception:
                raise _failure(
                    f"Target {name!r} failed contract {step_context.binding_name_to_type[name]!r}: {exception}",
                    phase="validation",
                    evaluation_started=True,
                ) from exception
        execution.phase = "assignment"
        step_context.record_assignment(name, value)
        return AssignmentResult(target_path, value, "output" if name in step_context.binding_commit_targets else "temporary")

    execution.phase = "target_resolution"
    try:
        target = step_context.environment.resolve(name)
        for field_name in parsed[1:-1]:
            target = getattr(target, field_name)
    except (AttributeError, NameError) as exception:
        raise _failure(
            f"Target {target_path!r} could not be resolved: {exception}", phase="target_resolution", evaluation_started=True
        ) from exception
    field_name = parsed[-1]
    if not isinstance(target, BaseModel):
        raise _failure(
            f"Unsupported assignment target {target_path!r}; use an ordinary Python helper", phase="target_resolution", evaluation_started=True
        )
    target_type = type(target)
    if (
        target_type.__setattr__ is not BaseModel.__setattr__
        or inspect.getattr_static(target_type, "_setattr_handler") is not inspect.getattr_static(BaseModel, "_setattr_handler")
        or not target_type.model_config.get("validate_assignment")
    ):
        raise _failure(
            f"Unsupported assignment target {target_path!r}; require a standard Pydantic model with validate_assignment=True",
            phase="target_resolution",
            evaluation_started=True,
        )
    if field_name not in target_type.model_fields or inspect.getattr_static(target_type, field_name, None) is not None:
        raise _failure(f"Target {target_path!r} is not an ordinary declared Pydantic field", phase="target_resolution", evaluation_started=True)
    execution.phase = "validation"
    try:
        setattr(target, field_name, value)
    except ValidationError as exception:
        raise _failure(
            f"Target {target_path!r} failed model assignment validation: {exception}", phase="validation", evaluation_started=True
        ) from exception
    # The standard setter already validated and wrote once. Read stored state directly,
    # without invoking a final getter or validating the field in isolation.
    value = object.__getattribute__(target, "__dict__")[field_name]
    if name in step_context.binding_commit_targets:
        step_context.record_output_binding_mutation(name)
    else:
        step_context.step_locals_revision += 1
    return AssignmentResult(target_path, value, "attribute")


async def assign_tool_async(step_context: StepContext, target_path: str, expression: str) -> AssignmentResult:
    request = new_operation_request(
        step_context,
        kind="assignment",
        target_path=target_path,
        expression=expression,
        expected_type=step_context.binding_name_to_type.get(target_path),
    )
    parsed: tuple[str, ...] = ()
    compiled: CodeType | None = None

    def admit() -> None:
        nonlocal parsed, compiled
        parsed = admit_assignment(step_context, target_path)
        compiled = compile_expression(expression)

    async def execute(execution: OperationExecution) -> AssignmentResult:
        assert compiled is not None
        value = await evaluate_compiled_expression(step_context, compiled)
        return assign_value(step_context, target_path, parsed, value, execution)

    return cast(AssignmentResult, await execute_operation_async(step_context, request, admit=admit, execute=execute))


def assign_tool(step_context: StepContext, target_path: str, expression: str) -> AssignmentResult:
    return run_coroutine_synchronously(lambda: assign_tool_async(step_context, target_path, expression))
