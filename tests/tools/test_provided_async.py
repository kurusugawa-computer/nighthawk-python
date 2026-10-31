from __future__ import annotations

import asyncio

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.provided import build_provided_tool_definitions
from tests.support.contexts import new_step_context


def _new_step_context() -> StepContext:
    return new_step_context(
        execution_reference=nh.ExecutionReference("test-run", "test-scope", "test_provided_async", "test_provided_async"),
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        output_binding_names=set(),
        read_binding_names=frozenset(),
        task_name_to_value={},
    )


def _new_run_context(step_context: StepContext) -> RunContext[StepContext]:
    return RunContext(
        deps=step_context,
        model=TestModel(),
        usage=RunUsage(),
    )


def _get_tool_function(name: str):  # type: ignore[no-untyped-def]
    definitions = build_provided_tool_definitions()
    for definition in definitions:
        if definition.name == name:
            return definition.tool.function
    raise ValueError(f"Tool {name!r} not found")


def test_nh_evaluate_awaits_async_expression() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = _new_step_context()
    step_context.environment.assign("calculate", calculate)
    run_context = _new_run_context(step_context)

    nh_evaluate = _get_tool_function("nh_evaluate")
    result = asyncio.run(nh_evaluate(run_context, expression="calculate(1, 2)"))
    assert result == 17


def test_nh_evaluate_handles_sync_expression() -> None:
    step_context = _new_step_context()
    step_context.environment.assign("x", 10)
    run_context = _new_run_context(step_context)

    nh_evaluate = _get_tool_function("nh_evaluate")
    result = asyncio.run(nh_evaluate(run_context, expression="x + 5"))
    assert result == 15


def test_nh_evaluate_supports_top_level_await() -> None:
    async def fetch(key: str) -> str:
        return f"value-{key}"

    step_context = _new_step_context()
    step_context.environment.assign("fetch", fetch)
    run_context = _new_run_context(step_context)

    nh_evaluate = _get_tool_function("nh_evaluate")
    result = asyncio.run(nh_evaluate(run_context, expression="await fetch('abc')"))
    assert result == "value-abc"


def test_nh_assign_awaits_async_expression() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = _new_step_context()
    step_context.environment.assign("calculate", calculate)
    run_context = _new_run_context(step_context)

    nh_assign = _get_tool_function("nh_assign")
    update = asyncio.run(nh_assign(run_context, target_path="result", expression="calculate(1, 2)"))

    assert update["updates"][0]["value"] == 17
    assert step_context.environment.resolve("result") == 17


def test_nh_assign_handles_sync_expression() -> None:
    step_context = _new_step_context()
    step_context.environment.assign("x", 10)
    run_context = _new_run_context(step_context)

    nh_assign = _get_tool_function("nh_assign")
    update = asyncio.run(nh_assign(run_context, target_path="result", expression="x * 3"))

    assert update["updates"][0]["value"] == 30
    assert step_context.environment.resolve("result") == 30
