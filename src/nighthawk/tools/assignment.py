from __future__ import annotations

import ast
import inspect
import types
from typing import Any, NoReturn, get_type_hints

from pydantic import BaseModel, TypeAdapter

from ..errors import ToolEvaluationError
from ..identifier_path import parse_identifier_path
from ..json_renderer import to_jsonable_value
from ..runtime.async_bridge import run_awaitable_value_synchronously
from ..runtime.step_context import StepContext
from .contracts import ToolBoundaryError


def _compile_expression(expression: str) -> types.CodeType:
    """Compile a Python expression with top-level await support."""
    return compile(
        expression,
        "<nighthawk-eval>",
        "eval",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )


async def eval_expression_async(step_context: StepContext, expression: str) -> object:
    """Evaluate a Python expression inside the step execution environment (async canonical).

    Security note: This uses ``eval()`` with ``ast.PyCF_ALLOW_TOP_LEVEL_AWAIT``
    to execute LLM-generated expressions against ``step_globals`` and ``step_locals``.
    Natural DSL sources are trusted, repository-managed assets.
    Do not wire untrusted user input into expressions evaluated here.
    """
    try:
        compiled_expression = _compile_expression(expression)
        value = eval(compiled_expression, step_context.step_globals, step_context.step_locals)
        if inspect.isawaitable(value):
            return await value
        return value
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


def eval_expression(step_context: StepContext, expression: str) -> object:
    """Evaluate a Python expression inside the step execution environment (sync wrapper)."""
    try:
        compiled_expression = _compile_expression(expression)
        value = eval(compiled_expression, step_context.step_globals, step_context.step_locals)
        return run_awaitable_value_synchronously(value)
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


def _raise_invalid_input(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryError(kind="invalid_input", message=message, guidance=guidance)


def _raise_resolution(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryError(kind="resolution", message=message, guidance=guidance)


def _raise_execution(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryError(kind="execution", message=message, guidance=guidance)


def _get_pydantic_field_type(model: BaseModel, field_name: str) -> object | None:
    model_fields = getattr(type(model), "model_fields", None)
    if model_fields is None:
        return None
    field = model_fields.get(field_name)
    if field is None:
        return None
    return field.annotation


def _get_annotation_type(instance: object, field_name: str) -> object | None:
    """Return the resolved type annotation for *field_name* on *instance*, or None.

    Works for dataclass instances and plain classes with ``__annotations__``.
    Returns ``None`` when the field has no annotation or when forward-reference
    resolution fails, signalling that type validation should be skipped.
    """
    try:
        hints = get_type_hints(type(instance))
    except Exception:
        return None
    return hints.get(field_name)


def _assign_value_to_target_path(
    *,
    step_context: StepContext,
    target_path: str,
    parsed_target_path: tuple[str, ...],
    value: object,
) -> dict[str, Any]:
    if len(parsed_target_path) == 1:
        name = parsed_target_path[0]

        if name in step_context.read_binding_names:
            _raise_invalid_input(
                message=f"Cannot rebind read binding '{name}' with nh_assign.",
                guidance=f"'{name}' is a read binding (<{name}>). To mutate it in-place, use nh_eval (e.g. {name}.update(...)). To rebind, declare it as a write binding (<:{name}>).",
            )

        expected_type = step_context.binding_name_to_type.get(name)

        if expected_type is not None:
            try:
                adapted = TypeAdapter(expected_type)
                value = adapted.validate_python(value)
            except Exception as e:
                _raise_invalid_input(
                    message=str(e),
                    guidance="Fix the value to match the expected type and retry.",
                )

        step_context.record_assignment(name, value)

        return {
            "target_path": target_path,
            "step_locals_revision": step_context.step_locals_revision,
            "updates": [{"path": target_path, "value": to_jsonable_value(value)}],
        }

    root_name = parsed_target_path[0]
    attribute_path = parsed_target_path[1:]

    if root_name not in step_context.step_locals:
        _raise_resolution(
            message=f"Unknown root name: {root_name}",
            guidance="Fix the target path so the referenced root name exists, then retry.",
        )
    root_object = step_context.step_locals[root_name]

    current_object = root_object
    for attribute in attribute_path[:-1]:
        try:
            current_object = getattr(current_object, attribute)
        except Exception as e:
            _raise_resolution(
                message=f"Failed to resolve attribute {attribute!r}: {e}",
                guidance="Fix the target path so the referenced attributes exist, then retry.",
            )

    final_attribute = attribute_path[-1]

    expected_type: object | None = None
    if isinstance(current_object, BaseModel):
        expected_type = _get_pydantic_field_type(current_object, final_attribute)
        if expected_type is None:
            _raise_invalid_input(
                message=f"Unknown field on {type(current_object).__name__}: {final_attribute}",
                guidance="Fix the target path so the referenced field exists, then retry.",
            )
    else:
        expected_type = _get_annotation_type(current_object, final_attribute)

    if expected_type is not None:
        try:
            adapted = TypeAdapter(expected_type)
            value = adapted.validate_python(value)
        except Exception as e:
            _raise_invalid_input(
                message=str(e),
                guidance="Fix the value to match the expected type and retry.",
            )

    try:
        setattr(current_object, final_attribute, value)
    except Exception as e:
        _raise_resolution(
            message=f"Failed to set attribute {final_attribute!r}: {e}",
            guidance="Fix the target path so the referenced attributes are assignable, then retry.",
        )

    # Dotted mutation bypasses record_assignment. Top-level rebinding still
    # drives ordinary assignment tracking, while write-binding roots touched by
    # dotted nh_assign are tracked separately for commit selection.
    if root_name in step_context.binding_commit_targets:
        step_context.record_output_binding_mutation(root_name)
    else:
        step_context.step_locals_revision += 1

    return {
        "target_path": target_path,
        "step_locals_revision": step_context.step_locals_revision,
        "updates": [{"path": target_path, "value": to_jsonable_value(value)}],
    }


def _resolve_value_for_assignment(step_context: StepContext, expression: str) -> object:
    try:
        return eval_expression(step_context, expression)
    except Exception as e:
        _raise_execution(
            message=str(e),
            guidance="Fix the expression and retry.",
        )


async def _resolve_value_for_assignment_async(step_context: StepContext, expression: str) -> object:
    try:
        return await eval_expression_async(step_context, expression)
    except Exception as e:
        _raise_execution(
            message=str(e),
            guidance="Fix the expression and retry.",
        )


def _validated_target_path(target_path: str) -> tuple[str, ...]:
    parsed = parse_identifier_path(target_path)
    if parsed is None:
        _raise_invalid_input(
            message="Invalid target_path; expected name(.field)* with ASCII identifiers",
            guidance="Fix target_path to match name(.field)* with ASCII identifiers and retry.",
        )
    return parsed


def assign_tool(
    step_context: StepContext,
    target_path: str,
    expression: str,
) -> dict[str, Any]:
    """Assign a computed value to a dotted target_path.

    Target grammar:
    - target_path := name ("." field)*

    Notes:
    - Any segment starting with "__" is forbidden.
    - On success, returns a JSON-serializable payload with keys `target_path`, `step_locals_revision`, and `updates`.
    - On failure, raises ToolBoundaryError.
    - The operation is atomic: on any failure, no updates are performed.
    """
    parsed_target_path = _validated_target_path(target_path)
    value = _resolve_value_for_assignment(step_context, expression)

    return _assign_value_to_target_path(
        step_context=step_context,
        target_path=target_path,
        parsed_target_path=parsed_target_path,
        value=value,
    )


async def assign_tool_async(
    step_context: StepContext,
    target_path: str,
    expression: str,
) -> dict[str, Any]:
    """Async version of assign_tool."""
    parsed_target_path = _validated_target_path(target_path)
    value = await _resolve_value_for_assignment_async(step_context, expression)

    return _assign_value_to_target_path(
        step_context=step_context,
        target_path=target_path,
        parsed_target_path=parsed_target_path,
        value=value,
    )
