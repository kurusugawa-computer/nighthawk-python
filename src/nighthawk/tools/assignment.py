"""Model-facing rendering over the common runtime operation entry points."""

from __future__ import annotations

from typing import Any

from ..json_renderer import to_jsonable_value
from ..runtime.assignment import AssignmentResult
from ..runtime.assignment import assign_tool as _assign_tool
from ..runtime.assignment import assign_tool_async as _assign_tool_async
from ..runtime.expression import eval_expression, eval_expression_async
from ..runtime.step_context import StepContext


def render_assignment_result(result: AssignmentResult) -> dict[str, Any]:
    return {
        "target_path": result.target_path,
        "retained_as": result.retained_as,
        "updates": [{"path": result.target_path, "value": to_jsonable_value(result.value)}],
    }


def assign_tool(step_context: StepContext, target_path: str, expression: str) -> dict[str, Any]:
    return render_assignment_result(_assign_tool(step_context, target_path, expression))


async def assign_tool_async(step_context: StepContext, target_path: str, expression: str) -> dict[str, Any]:
    return render_assignment_result(await _assign_tool_async(step_context, target_path, expression))


__all__ = ["assign_tool", "assign_tool_async", "eval_expression", "eval_expression_async"]
