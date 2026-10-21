"""Model-facing rendering over the common runtime operation entry points."""

from __future__ import annotations

from typing import Any

from ..json_renderer import to_jsonable_value
from ..runtime.assignment import AssignmentResult, assign_expression, assign_expression_async
from ..runtime.step_context import StepContext


def render_assignment_result(result: AssignmentResult) -> dict[str, Any]:
    return {
        "target_path": result.target_path,
        "retained_as": result.retained_as,
        "updates": [{"path": result.target_path, "value": to_jsonable_value(result.value)}],
    }


def assign_tool(step_context: StepContext, target_path: str, expression: str) -> dict[str, Any]:
    return render_assignment_result(assign_expression(step_context, target_path, expression))


async def assign_tool_async(step_context: StepContext, target_path: str, expression: str) -> dict[str, Any]:
    return render_assignment_result(await assign_expression_async(step_context, target_path, expression))
