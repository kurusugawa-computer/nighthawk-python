from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from ..errors import ToolEvaluationError, ToolValidationError
from ..execution.context import ExecutionContext


def eval_expression(execution_context: ExecutionContext, expression: str) -> object:
    try:
        return eval(expression, execution_context.globals, execution_context.locals)
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


def summarize(value: object) -> str:
    text = repr(value)
    if len(text) > 200:
        return text[:200] + "..."
    return text


def assign_tool(
    execution_context: ExecutionContext,
    target: str,
    expression: str,
    *,
    type_hints: dict[str, Any],
) -> dict[str, Any]:
    """Assign into context locals or memory.

    Target forms:
    - Local: <name>
    - Memory: memory.<field>
    """

    try:
        value = eval_expression(execution_context, expression)
    except ToolEvaluationError as e:
        return {"ok": False, "error": str(e)}

    if target.startswith("<") and target.endswith(">"):
        name = target[1:-1]
        try:
            name.encode("ascii")
        except UnicodeEncodeError:
            return {"ok": False, "error": f"Local target must be ASCII: {name!r}"}

        if not name.isidentifier():
            return {"ok": False, "error": f"Invalid local target: {name!r}"}

        if name == "memory" or name.startswith("__"):
            return {"ok": False, "error": f"Reserved local target: {name!r}"}

        hinted = type_hints.get(name)
        if hinted is not None:
            try:
                adapted = TypeAdapter(hinted)
                value = adapted.validate_python(value)
            except Exception as e:
                raise ToolValidationError(f"Validation failed for {name}: {e}") from e

        execution_context.locals[name] = value
        return {"ok": True, "target": name, "value": summarize(value)}

    if target.startswith("memory."):
        if execution_context.memory is None:
            return {"ok": False, "error": "Memory is not enabled"}
        field = target.split(".", 1)[1]
        if not field.isidentifier():
            return {"ok": False, "error": "Invalid memory field"}

        if not hasattr(execution_context.memory, field):
            return {"ok": False, "error": f"Unknown memory field: {field}"}

        field_type = execution_context.memory.model_fields[field].annotation
        try:
            adapted = TypeAdapter(field_type)
            coerced = adapted.validate_python(value)
        except Exception as e:
            raise ToolValidationError(f"Validation failed for memory.{field}: {e}") from e

        try:
            setattr(execution_context.memory, field, coerced)
        except Exception as e:
            raise ToolValidationError(f"Failed to set memory.{field}: {e}") from e

        return {"ok": True, "target": f"memory.{field}", "value": summarize(coerced)}

    return {"ok": False, "error": "Invalid target"}
