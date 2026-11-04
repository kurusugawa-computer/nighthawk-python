from __future__ import annotations

from functools import cache

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from ..json_renderer import to_jsonable_value
from ..runtime.assignment import assign_expression_async
from ..runtime.expression import evaluate_expression_async
from ..runtime.step_context import StepContext


@cache
def get_provided_tools() -> tuple[Tool[StepContext], ...]:
    async def nh_assign(
        run_context: RunContext[StepContext],
        target_path: str,
        expression: str,
    ) -> dict[str, object]:
        result = await assign_expression_async(run_context.deps, target_path, expression)
        return {
            "target_path": result.target_path,
            "retained_as": result.retained_as,
            "updates": [{"path": result.target_path, "value": to_jsonable_value(result.value)}],
        }

    async def nh_evaluate(run_context: RunContext[StepContext], expression: str) -> object:
        return await evaluate_expression_async(run_context.deps, expression)

    return (
        Tool(
            nh_assign,
            metadata={"nighthawk.provided": True, "nighthawk.operation_kind": "assignment"},
            description="Assign a Python expression to a declared output, a new temporary name, or a Pydantic field with assignment validation enabled. target_path: name(.field)*.",
        ),
        Tool(
            nh_evaluate,
            metadata={"nighthawk.provided": True, "nighthawk.operation_kind": "evaluation"},
            description="Evaluate a Python expression and return the result. Examples: len(items), data.get('key', 0), items.sort(), add(3, 7).",
        ),
    )
