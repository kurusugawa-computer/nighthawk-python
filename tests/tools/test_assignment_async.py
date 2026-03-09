from __future__ import annotations

import asyncio

import pytest

from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.assignment import assign_tool, assign_tool_async, eval_expression_async
from nighthawk.tools.contracts import ToolBoundaryFailure


def _new_step_context() -> StepContext:
    return StepContext(
        step_id="test_assignment_async",
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
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


def test_assign_rejects_rebind_of_read_binding() -> None:
    step_context = StepContext(
        step_id="test_read_guard",
        step_globals={"__builtins__": __builtins__},
        step_locals={"data": {"key": "old"}},
        binding_commit_targets=set(),
        read_binding_names=frozenset({"data"}),
    )

    with pytest.raises(ToolBoundaryFailure):
        assign_tool(step_context, "data", "{'key': 'new'}")

    assert step_context.step_locals["data"] == {"key": "old"}


def test_assign_allows_rebind_when_both_read_and_write_binding() -> None:
    step_context = StepContext(
        step_id="test_read_write",
        step_globals={"__builtins__": __builtins__},
        step_locals={"data": {"key": "old"}},
        binding_commit_targets={"data"},
        read_binding_names=frozenset(),
    )

    result = assign_tool(step_context, "data", "{'key': 'new'}")

    assert result["updates"][0]["value"] == {"key": "new"}
    assert step_context.step_locals["data"] == {"key": "new"}
    assert "data" in step_context.assigned_binding_names


def test_assign_allows_multiple_rebinds_of_new_local() -> None:
    step_context = StepContext(
        step_id="test_new_local",
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
    )

    assign_tool(step_context, "temp", "1")
    assert step_context.step_locals["temp"] == 1

    assign_tool(step_context, "temp", "2")
    assert step_context.step_locals["temp"] == 2
