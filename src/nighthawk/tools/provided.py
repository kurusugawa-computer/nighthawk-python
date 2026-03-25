from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from ..runtime.step_context import StepContext
from .assignment import assign_tool, eval_expression
from .contracts import ToolBoundaryError


@dataclass(frozen=True)
class ProvidedToolDefinition:
    name: str
    tool: Tool[StepContext]


def _eval_expression_or_raise(run_context: RunContext[StepContext], expression: str) -> object:
    try:
        return eval_expression(run_context.deps, expression)
    except Exception as exception:
        raise ToolBoundaryError(kind="execution", message=str(exception), guidance="Fix the expression and retry.") from exception


def build_provided_tool_definitions() -> list[ProvidedToolDefinition]:
    metadata = {"nighthawk.provided": True}

    def nh_assign(
        run_context: RunContext[StepContext],
        target_path: str,
        expression: str,
    ) -> dict[str, Any]:
        return assign_tool(
            run_context.deps,
            target_path,
            expression,
        )

    def nh_eval(run_context: RunContext[StepContext], expression: str) -> object:
        return _eval_expression_or_raise(run_context, expression)

    return [
        ProvidedToolDefinition(
            name="nh_assign",
            tool=Tool(
                nh_assign,
                name="nh_assign",
                metadata=metadata,
                description="Set a write binding to a new value. target_path: a name or dotted path (e.g., 'result', 'obj.field'). expression: evaluated as Python.",
            ),
        ),
        ProvidedToolDefinition(
            name="nh_eval",
            tool=Tool(
                nh_eval,
                name="nh_eval",
                metadata=metadata,
                description="Evaluate a Python expression and return the result. Examples: len(items), data.get('key', 0), items.sort(), add(3, 7).",
            ),
        ),
    ]
