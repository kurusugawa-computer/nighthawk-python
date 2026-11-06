"""Tracked output and temporary writes, plus validated Pydantic field assignment."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import CodeType
from typing import Literal, cast

from pydantic import BaseModel, TypeAdapter, ValidationError
from typing_extensions import TypeForm

from nighthawk.core.async_bridge import run_coroutine_synchronously
from nighthawk.core.expression import compile_expression, evaluate_compiled_expression
from nighthawk.core.operation_contract import OperationError
from nighthawk.core.operations import OperationExecution, execute_operation_async, new_operation_request
from nighthawk.core.step_context import StepContext


@dataclass(frozen=True)
class AssignmentResult:
    """The validated live value retained by one assignment operation.

    ``retained_as`` distinguishes a pending output, a step temporary, and an immediate attribute mutation.
    Output assignment here updates working state; final writeback to Python still requires step finalization.
    """

    target_path: str
    value: object
    retained_as: Literal["temporary", "output", "attribute"]


def _failure(execution: OperationExecution, message: str) -> OperationError:
    return execution.diagnose(
        message,
        guidance=(
            "Earlier right-hand-side or validator effects may remain. Correct the target or value; use an ordinary Python helper for unsupported mutations."
            if execution.evaluation_started
            else "Evaluation has not started. Fix the target; existing Python names require an output declaration."
        ),
    )


def _admit_assignment(step_context: StepContext, target_path: str, execution: OperationExecution) -> tuple[str, ...]:
    parsed = _parse_identifier_path(target_path)
    if parsed is None:
        raise _failure(execution, "Invalid target_path; expected name(.field)* with ASCII identifiers and no __ segments")
    try:
        if len(parsed) == 1:
            step_context.environment._check_assignment(parsed[0])
        else:
            step_context.environment.resolve(parsed[0])
    except (ValueError, NameError) as exception:
        raise _failure(execution, str(exception)) from exception
    return parsed


def _assign_value(
    step_context: StepContext, target_path: str, parsed: tuple[str, ...], value: object, execution: OperationExecution
) -> AssignmentResult:
    name = parsed[0]
    if len(parsed) == 1:
        execution.phase = "validation"
        if name in step_context.environment.binding_name_to_type:
            try:
                value = TypeAdapter(cast(TypeForm[object], step_context.environment.binding_name_to_type[name])).validate_python(value)
            except ValidationError as exception:
                raise _failure(
                    execution,
                    f"Target {name!r} failed contract {step_context.environment.binding_name_to_type[name]!r}: {exception}",
                ) from exception
        execution.phase = "assignment"
        step_context.environment.assign(name, value)
        return AssignmentResult(target_path, value, "output" if name in step_context.environment.output_name_set else "temporary")

    execution.phase = "target_resolution"
    try:
        target = step_context.environment.resolve(name)
        for field_name in parsed[1:-1]:
            target = getattr(target, field_name)
    except (AttributeError, NameError) as exception:
        raise _failure(execution, f"Target {target_path!r} could not be resolved: {exception}") from exception
    field_name = parsed[-1]
    if not isinstance(target, BaseModel):
        raise _failure(execution, f"Unsupported assignment target {target_path!r}; use an ordinary Python helper")
    target_type = type(target)
    if (
        target_type.__setattr__ is not BaseModel.__setattr__
        or inspect.getattr_static(target_type, "_setattr_handler") is not inspect.getattr_static(BaseModel, "_setattr_handler")
        or not target_type.model_config.get("validate_assignment")
    ):
        raise _failure(
            execution,
            f"Unsupported assignment target {target_path!r}; require a standard Pydantic model with validate_assignment=True",
        )
    if field_name not in target_type.model_fields or inspect.getattr_static(target_type, field_name, None) is not None:
        raise _failure(execution, f"Target {target_path!r} is not an ordinary declared Pydantic field")
    execution.phase = "validation"
    try:
        setattr(target, field_name, value)
    except ValidationError as exception:
        raise _failure(execution, f"Target {target_path!r} failed model assignment validation: {exception}") from exception
    # The standard setter already validated and wrote once. Read stored state directly,
    # without invoking a final getter or validating the field in isolation.
    value = object.__getattribute__(target, "__dict__")[field_name]
    return AssignmentResult(target_path, value, "attribute")


async def assign_expression_async(step_context: StepContext, target_path: str, expression: str) -> AssignmentResult:
    """Evaluate an expression once and assign its result through the operation boundary.

    ``target_path`` selects a declared output, a new temporary name, or a standard Pydantic field with assignment validation.
    The expression uses the context's live environment and awaits an awaitable result once.
    Admission and oversight precede evaluation; validation and operation notification follow it.
    Recoverable operation errors and rejections retain their structured diagnostics.
    Application exceptions propagate, and earlier effects are not rolled back.
    """

    request = new_operation_request(
        step_context,
        kind="assignment",
        target_path=target_path,
        expression=expression,
        expected_type=step_context.environment.binding_name_to_type.get(target_path),
    )
    parsed: tuple[str, ...] = ()
    compiled: CodeType | None = None

    def admit(execution: OperationExecution) -> None:
        nonlocal parsed, compiled
        parsed = _admit_assignment(step_context, target_path, execution)
        compiled = compile_expression(expression, execution)

    async def execute(execution: OperationExecution) -> AssignmentResult:
        assert compiled is not None
        value = await evaluate_compiled_expression(step_context, compiled, execution)
        return _assign_value(step_context, target_path, parsed, value, execution)

    return cast(AssignmentResult, await execute_operation_async(step_context, request, admit=admit, execute=execute))


def assign_expression(step_context: StepContext, target_path: str, expression: str) -> AssignmentResult:
    """Synchronous counterpart of assign_expression_async, including its validation and effect boundaries."""
    return run_coroutine_synchronously(lambda: assign_expression_async(step_context, target_path, expression))


def _parse_identifier_path(path: str) -> tuple[str, ...] | None:
    """Return the segments of a dotted ASCII identifier path, or ``None`` when any segment is invalid or dunder-prefixed."""
    if not path:
        return None

    parts = path.split(".")
    if any(part == "" for part in parts):
        return None

    for part in parts:
        try:
            part.encode("ascii")
        except UnicodeEncodeError:
            return None
        if not part.isidentifier():
            return None
        if part.startswith("__"):
            return None

    return tuple(parts)
