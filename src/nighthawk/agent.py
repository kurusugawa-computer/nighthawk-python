from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter
from pydantic_ai import Agent

from .configuration import Configuration
from .errors import ToolEvaluationError, ToolValidationError


@dataclass
class ToolContext:
    context_globals: dict[str, object]
    context_locals: dict[str, object]
    allowed_binding_targets: set[str]
    memory: BaseModel | None


def dir_tool(tool_context: ToolContext, expression: str) -> str:
    value = _eval_expr(tool_context, expression)
    return "\n".join(builtins.dir(value))


def help_tool(tool_context: ToolContext, expression: str) -> str:
    value = _eval_expr(tool_context, expression)
    # Capture help() output deterministically by using pydoc.render_doc
    import pydoc

    return pydoc.render_doc(value, renderer=pydoc.plaintext)


def eval_tool(tool_context: ToolContext, expression: str) -> str:
    value = _eval_expr(tool_context, expression)
    try:
        return json.dumps(value, default=repr)
    except Exception:
        return json.dumps(repr(value))


def assign_tool(tool_context: ToolContext, target: str, expression: str, *, type_hints: dict[str, Any]) -> dict[str, Any]:
    """Assign into context locals or memory.

    Target forms:
    - Local: <name>
    - Memory: memory.<field>
    """

    try:
        value = _eval_expr(tool_context, expression)
    except ToolEvaluationError as e:
        return {"ok": False, "error": str(e)}

    if target.startswith("<") and target.endswith(">"):
        name = target[1:-1]
        if name not in tool_context.allowed_binding_targets:
            return {"ok": False, "error": f"Target not allowed: {name}"}

        hinted = type_hints.get(name)
        if hinted is not None:
            try:
                adapted = TypeAdapter(hinted)
                value = adapted.validate_python(value)
            except Exception as e:
                raise ToolValidationError(f"Validation failed for {name}: {e}") from e

        tool_context.context_locals[name] = value
        return {"ok": True, "target": name, "value": _summarize(value)}

    if target.startswith("memory."):
        if tool_context.memory is None:
            return {"ok": False, "error": "Memory is not enabled"}
        field = target.split(".", 1)[1]
        if not field.isidentifier():
            return {"ok": False, "error": "Invalid memory field"}

        if not hasattr(tool_context.memory, field):
            return {"ok": False, "error": f"Unknown memory field: {field}"}

        field_type = tool_context.memory.model_fields[field].annotation
        try:
            adapted = TypeAdapter(field_type)
            coerced = adapted.validate_python(value)
        except Exception as e:
            raise ToolValidationError(f"Validation failed for memory.{field}: {e}") from e

        try:
            setattr(tool_context.memory, field, coerced)
        except Exception as e:
            raise ToolValidationError(f"Failed to set memory.{field}: {e}") from e

        return {"ok": True, "target": f"memory.{field}", "value": _summarize(coerced)}

    return {"ok": False, "error": "Invalid target"}


def _eval_expr(tool_context: ToolContext, expression: str) -> object:
    try:
        return eval(expression, tool_context.context_globals, tool_context.context_locals)
    except Exception as e:
        raise ToolEvaluationError(str(e)) from e


def _summarize(value: object) -> str:
    text = repr(value)
    if len(text) > 200:
        return text[:200] + "..."
    return text


class NaturalEffect(BaseModel, extra="forbid"):
    """Final response 'effect' object."""

    type: Literal["continue", "break", "return"]
    value_json: str | None = None


class NaturalError(BaseModel, extra="forbid"):
    """Final response 'error' object."""

    message: str
    type: str | None = None


class NaturalFinal(BaseModel, extra="forbid"):
    effect: NaturalEffect | None = None
    error: NaturalError | None = None


type NaturalAgent = Agent[ToolContext, NaturalFinal]


def make_agent(configuration: Configuration) -> NaturalAgent:
    agent: NaturalAgent = Agent(
        model=configuration.model,
        output_type=NaturalFinal,
        deps_type=ToolContext,
    )

    return agent
