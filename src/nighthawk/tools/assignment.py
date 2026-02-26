from __future__ import annotations

import ast
import inspect
from typing import Any, NoReturn

from pydantic import BaseModel, TypeAdapter

from ..errors import ToolEvaluationError
from ..json_renderer import to_jsonable_value
from ..runtime.async_bridge import run_awaitable_value_synchronously
from ..runtime.step_context import StepContext
from .contracts import ToolBoundaryFailure


def eval_expression(step_context: StepContext, expression: str) -> object:
    try:
        compiled_expression = compile(
            expression,
            "<nighthawk-eval>",
            "eval",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )
        value = eval(compiled_expression, step_context.step_globals, step_context.step_locals)
        return run_awaitable_value_synchronously(value)
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


async def eval_expression_async(step_context: StepContext, expression: str) -> object:
    try:
        compiled_expression = compile(
            expression,
            "<nighthawk-eval>",
            "eval",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )
        value = eval(compiled_expression, step_context.step_globals, step_context.step_locals)
        if inspect.isawaitable(value):
            return await value
        return value
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


def _raise_invalid_input(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryFailure(kind="invalid_input", message=message, guidance=guidance)


def _raise_resolution(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryFailure(kind="resolution", message=message, guidance=guidance)


def _raise_execution(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryFailure(kind="execution", message=message, guidance=guidance)


def _to_jsonable_value(value: object) -> object:
    return to_jsonable_value(value)


def _parse_target_path(target_path: str) -> tuple[str, ...] | None:
    if not target_path:
        return None

    parts = target_path.split(".")
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


def _get_pydantic_field_type(model: BaseModel, field_name: str) -> object | None:
    model_fields = getattr(type(model), "model_fields", None)
    if model_fields is None:
        return None
    field = model_fields.get(field_name)
    if field is None:
        return None
    return field.annotation


def _assign_value_to_target_path(
    *,
    step_context: StepContext,
    target_path: str,
    parsed_target_path: tuple[str, ...],
    value: object,
) -> dict[str, Any]:
    if len(parsed_target_path) == 1:
        name = parsed_target_path[0]
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

        step_context.step_locals[name] = value
        step_context.assigned_binding_names.add(name)
        step_context.step_locals_revision += 1

        update: dict[str, Any] = {"path": target_path}
        update["value"] = _to_jsonable_value(value)

        return {
            "target_path": target_path,
            "step_locals_revision": step_context.step_locals_revision,
            "updates": [update],
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

    step_context.step_locals_revision += 1

    update: dict[str, Any] = {"path": target_path}
    update["value"] = _to_jsonable_value(value)

    return {
        "target_path": target_path,
        "step_locals_revision": step_context.step_locals_revision,
        "updates": [update],
    }


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
    - On failure, raises ToolBoundaryFailure.
    - The operation is atomic: on any failure, no updates are performed.
    """

    parsed_target_path = _parse_target_path(target_path)
    if parsed_target_path is None:
        _raise_invalid_input(
            message="Invalid target_path; expected name(.field)* with ASCII identifiers",
            guidance="Fix target_path to match name(.field)* with ASCII identifiers and retry.",
        )

    try:
        value = eval_expression(step_context, expression)
    except Exception as e:
        _raise_execution(
            message=str(e),
            guidance="Fix the expression and retry.",
        )

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
    parsed_target_path = _parse_target_path(target_path)
    if parsed_target_path is None:
        _raise_invalid_input(
            message="Invalid target_path; expected name(.field)* with ASCII identifiers",
            guidance="Fix target_path to match name(.field)* with ASCII identifiers and retry.",
        )

    try:
        value = await eval_expression_async(step_context, expression)
    except Exception as e:
        _raise_execution(
            message=str(e),
            guidance="Fix the expression and retry.",
        )

    return _assign_value_to_target_path(
        step_context=step_context,
        target_path=target_path,
        parsed_target_path=parsed_target_path,
        value=value,
    )
