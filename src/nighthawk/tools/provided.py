from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from ..runtime.expression import evaluate_expression_async
from ..runtime.step_context import StepContext
from .assignment import assign_tool_async


@dataclass(frozen=True)
class ProvidedToolDefinition:
    name: str
    tool: Tool[StepContext]


def build_provided_tool_definitions() -> list[ProvidedToolDefinition]:
    async def nh_assign(
        run_context: RunContext[StepContext],
        target_path: str,
        expression: str,
    ) -> dict[str, Any]:
        return await assign_tool_async(
            run_context.deps,
            target_path,
            expression,
        )

    async def nh_evaluate(run_context: RunContext[StepContext], expression: str) -> object:
        return await evaluate_expression_async(run_context.deps, expression)

    return [
        ProvidedToolDefinition(
            name="nh_assign",
            tool=Tool(
                nh_assign,
                name="nh_assign",
                metadata={"nighthawk.provided": True, "nighthawk.operation_kind": "assignment"},
                description="Assign a Python expression to a declared output, a new temporary name, or a Pydantic field with assignment validation enabled. target_path: name(.field)*.",
            ),
        ),
        ProvidedToolDefinition(
            name="nh_evaluate",
            tool=Tool(
                nh_evaluate,
                name="nh_evaluate",
                metadata={"nighthawk.provided": True, "nighthawk.operation_kind": "evaluation"},
                description="Evaluate a Python expression and return the result. Examples: len(items), data.get('key', 0), items.sort(), add(3, 7).",
            ),
        ),
    ]
