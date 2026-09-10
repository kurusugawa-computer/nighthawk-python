from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from ..runtime.step_context import StepContext
from .assignment import assign_tool_async, eval_expression_async


@dataclass(frozen=True)
class ProvidedToolDefinition:
    name: str
    tool: Tool[StepContext]


async def _eval_expression_or_raise_async(run_context: RunContext[StepContext], expression: str) -> object:
    return await eval_expression_async(run_context.deps, expression)


def build_provided_tool_definitions() -> list[ProvidedToolDefinition]:
    metadata = {"nighthawk.provided": True}

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

    async def nh_eval(run_context: RunContext[StepContext], expression: str) -> object:
        return await _eval_expression_or_raise_async(run_context, expression)

    return [
        ProvidedToolDefinition(
            name="nh_assign",
            tool=Tool(
                nh_assign,
                name="nh_assign",
                metadata=metadata,
                description="Assign a Python expression to a declared output, a new temporary name, or a Pydantic field with assignment validation enabled. target_path: name(.field)*.",
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
