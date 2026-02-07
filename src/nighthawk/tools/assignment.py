from __future__ import annotations

from typing import Any, NoReturn

from pydantic import BaseModel, TypeAdapter
from pydantic_core import to_jsonable_python

from ..errors import ToolEvaluationError
from ..execution.context import ExecutionContext
from .contracts import ToolBoundaryFailure


def eval_expression(execution_context: ExecutionContext, expression: str) -> object:
    try:
        return eval(expression, execution_context.execution_globals, execution_context.execution_locals)
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


def _raise_invalid_input(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryFailure(kind="invalid_input", message=message, guidance=guidance)


def _raise_resolution(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryFailure(kind="resolution", message=message, guidance=guidance)


def _raise_execution(*, message: str, guidance: str) -> NoReturn:
    raise ToolBoundaryFailure(kind="execution", message=message, guidance=guidance)


def _to_jsonable_value(value: object) -> object:
    try:
        return to_jsonable_python(
            value,
            serialize_unknown=True,
            fallback=lambda v: f"<{type(v).__name__}>",
        )
    except Exception:
        return f"<{type(value).__name__}>"


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

    if parts == ["memory"]:
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


def assign_tool(
    execution_context: ExecutionContext,
    target_path: str,
    expression: str,
) -> dict[str, Any]:
    """Assign a computed value to a dotted target_path.

    Target grammar:
    - target_path := name ("." field)*

    Notes:
    - Assigning to the root name "memory" is forbidden.
    - Any segment starting with "__" is forbidden.
    - On success, returns a JSON-serializable payload with keys `target_path`, `execution_locals_revision`, and `updates`.
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
        value = eval_expression(execution_context, expression)
    except Exception as e:
        _raise_execution(
            message=str(e),
            guidance="Fix the expression and retry.",
        )

    if len(parsed_target_path) == 1:
        name = parsed_target_path[0]
        expected_type = execution_context.binding_name_to_type.get(name)

        if expected_type is not None:
            try:
                adapted = TypeAdapter(expected_type)
                value = adapted.validate_python(value)
            except Exception as e:
                _raise_invalid_input(
                    message=str(e),
                    guidance="Fix the value to match the expected type and retry.",
                )

        execution_context.execution_locals[name] = value
        execution_context.assigned_binding_names.add(name)
        execution_context.execution_locals_revision += 1

        update: dict[str, Any] = {"path": target_path}

        update["value"] = _to_jsonable_value(value)

        return {
            "target_path": target_path,
            "execution_locals_revision": execution_context.execution_locals_revision,
            "updates": [update],
        }

    root_name = parsed_target_path[0]
    attribute_path = parsed_target_path[1:]

    if root_name == "memory":
        if execution_context.memory is None:
            _raise_execution(
                message="Memory is not enabled",
                guidance="Enable memory or assign to a non-memory target, then retry.",
            )
        root_object: object = execution_context.memory
    else:
        if root_name not in execution_context.execution_locals:
            _raise_resolution(
                message=f"Unknown root name: {root_name}",
                guidance="Fix the target path so the referenced root name exists, then retry.",
            )
        root_object = execution_context.execution_locals[root_name]

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

    execution_context.execution_locals_revision += 1

    update: dict[str, Any] = {"path": target_path}

    update["value"] = _to_jsonable_value(value)

    return {
        "target_path": target_path,
        "execution_locals_revision": execution_context.execution_locals_revision,
        "updates": [update],
    }
