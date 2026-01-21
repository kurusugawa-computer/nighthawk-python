from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter

from .errors import ToolEvaluationError, ToolValidationError


@dataclass
class ToolContext:
    context_globals: dict[str, object]
    context_locals: dict[str, object]
    allowed_local_targets: set[str]
    memory: BaseModel | None


def dir_tool(ctx: ToolContext, expr: str) -> str:
    obj = _eval_expr(ctx, expr)
    return "\n".join(builtins.dir(obj))


def help_tool(ctx: ToolContext, expr: str) -> str:
    obj = _eval_expr(ctx, expr)
    # Capture help() output deterministically by using pydoc.render_doc
    import pydoc

    return pydoc.render_doc(obj, renderer=pydoc.plaintext)


def eval_tool(ctx: ToolContext, expr: str) -> str:
    obj = _eval_expr(ctx, expr)
    try:
        return json.dumps(obj, default=repr)
    except Exception:
        return json.dumps(repr(obj))


def assign_tool(ctx: ToolContext, target: str, expression: str, *, type_hints: dict[str, Any]) -> dict[str, Any]:
    """Assign into context locals or memory.

    Target forms:
    - Local: <name>
    - Memory: memory.<field>
    """

    try:
        value = _eval_expr(ctx, expression)
    except ToolEvaluationError as e:
        return {"ok": False, "error": str(e)}

    if target.startswith("<") and target.endswith(">"):
        name = target[1:-1]
        if name not in ctx.allowed_local_targets:
            return {"ok": False, "error": f"Target not allowed: {name}"}

        hinted = type_hints.get(name)
        if hinted is not None:
            try:
                adapted = TypeAdapter(hinted)
                value = adapted.validate_python(value)
            except Exception as e:
                raise ToolValidationError(f"Validation failed for {name}: {e}") from e

        ctx.context_locals[name] = value
        return {"ok": True, "target": name, "value": _summarize(value)}

    if target.startswith("memory."):
        if ctx.memory is None:
            return {"ok": False, "error": "Memory is not enabled"}
        field = target.split(".", 1)[1]
        if not field.isidentifier():
            return {"ok": False, "error": "Invalid memory field"}

        if not hasattr(ctx.memory, field):
            return {"ok": False, "error": f"Unknown memory field: {field}"}

        field_type = ctx.memory.model_fields[field].annotation
        try:
            adapted = TypeAdapter(field_type)
            coerced = adapted.validate_python(value)
        except Exception as e:
            raise ToolValidationError(f"Validation failed for memory.{field}: {e}") from e

        try:
            setattr(ctx.memory, field, coerced)
        except Exception as e:
            raise ToolValidationError(f"Failed to set memory.{field}: {e}") from e

        return {"ok": True, "target": f"memory.{field}", "value": _summarize(coerced)}

    return {"ok": False, "error": "Invalid target"}


def _eval_expr(ctx: ToolContext, expr: str) -> object:
    try:
        return eval(expr, ctx.context_globals, ctx.context_locals)
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


def _summarize(value: object) -> str:
    s = repr(value)
    if len(s) > 200:
        return s[:200] + "..."
    return s
