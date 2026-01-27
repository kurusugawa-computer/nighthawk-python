from __future__ import annotations

from typing import Any

from pydantic import BaseModel, TypeAdapter

from ..errors import ToolEvaluationError
from ..execution.context import ExecutionContext


def eval_expression(execution_context: ExecutionContext, expression: str) -> object:
    try:
        return eval(expression, execution_context.execution_globals, execution_context.execution_locals)
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


def summarize(value: object) -> str:
    text = repr(value)
    if len(text) > 200:
        return text[:200] + "..."
    return text


def _error(target: str, error_type: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "target": target,
        "error": {
            "type": error_type,
            "message": message,
        },
    }


def _parse_target(target: str) -> tuple[str, ...] | None:
    if not target:
        return None

    parts = target.split(".")
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
    target: str,
    expression: str,
) -> dict[str, Any]:
    """Assign a computed value to a dotted target.

    Target grammar:
    - target := name ("." field)*

    Notes:
    - Assigning to the root name "memory" is forbidden.
    - Any segment starting with "__" is forbidden.
    - All failures return a diagnostic object; this function never raises.
    - The operation is atomic: on any failure, no updates are performed.
    """

    parsed_target = _parse_target(target)
    if parsed_target is None:
        return _error(target, "parse", "Invalid target; expected name(.field)* with ASCII identifiers")

    try:
        value = eval_expression(execution_context, expression)
    except Exception as e:
        return _error(target, "evaluation", str(e))

    if len(parsed_target) == 1:
        name = parsed_target[0]
        expected_type = execution_context.binding_name_to_type.get(name)

        if expected_type is not None:
            try:
                adapted = TypeAdapter(expected_type)
                value = adapted.validate_python(value)
            except Exception as e:
                return _error(target, "validation", str(e))

        execution_context.execution_locals[name] = value
        return {
            "ok": True,
            "target": target,
            "value": summarize(value),
        }

    root_name = parsed_target[0]
    attribute_path = parsed_target[1:]

    if root_name == "memory":
        if execution_context.memory is None:
            return _error(target, "memory", "Memory is not enabled")
        root_object: object = execution_context.memory
    else:
        if root_name not in execution_context.execution_locals:
            return _error(target, "traversal", f"Unknown root name: {root_name}")
        root_object = execution_context.execution_locals[root_name]

    current_object = root_object
    for attribute in attribute_path[:-1]:
        try:
            current_object = getattr(current_object, attribute)
        except Exception as e:
            return _error(target, "traversal", f"Failed to resolve attribute {attribute!r}: {e}")

    final_attribute = attribute_path[-1]

    expected_type: object | None = None
    if isinstance(current_object, BaseModel):
        expected_type = _get_pydantic_field_type(current_object, final_attribute)
        if expected_type is None:
            return _error(target, "validation", f"Unknown field on {type(current_object).__name__}: {final_attribute}")

    if expected_type is not None:
        try:
            adapted = TypeAdapter(expected_type)
            value = adapted.validate_python(value)
        except Exception as e:
            return _error(target, "validation", str(e))

    try:
        setattr(current_object, final_attribute, value)
    except Exception as e:
        return _error(target, "traversal", f"Failed to set attribute {final_attribute!r}: {e}")

    return {
        "ok": True,
        "target": target,
        "value": summarize(value),
    }
