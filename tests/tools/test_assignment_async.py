from __future__ import annotations

import asyncio

from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.assignment import assign_tool_async, eval_expression_async


def _new_step_context() -> StepContext:
    return StepContext(
        step_id="test_assignment_async",
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
    )


def test_eval_expression_async_supports_top_level_await() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = _new_step_context()
    step_context.step_locals["calculate"] = calculate

    result = asyncio.run(eval_expression_async(step_context, "await calculate(1, 2)"))
    assert result == 17


def test_eval_expression_async_awaits_returned_awaitable_implicitly() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = _new_step_context()
    step_context.step_locals["calculate"] = calculate

    result = asyncio.run(eval_expression_async(step_context, "calculate(1, 2)"))
    assert result == 17


def test_assign_tool_async_assigns_awaited_result() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = _new_step_context()
    step_context.step_locals["calculate"] = calculate

    update = asyncio.run(assign_tool_async(step_context, "result", "await calculate(1, 2)"))

    assert update["updates"][0]["value"] == 17
    assert step_context.step_locals["result"] == 17
