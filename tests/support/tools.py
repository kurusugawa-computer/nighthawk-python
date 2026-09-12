from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.contracts import (
    ToolError,
    ToolHandlerResult,
    ToolOutcome,
)


def _new_step_context() -> StepContext:
    return StepContext(
        execution_reference=nh.ExecutionReference("test-run", "test-scope", "test_tool_boundary", "test_tool_boundary"),
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        task_name_to_value={},
    )


def _new_run_context() -> RunContext[StepContext]:
    return RunContext(
        deps=_new_step_context(),
        model=TestModel(),
        usage=RunUsage(),
    )


def _test_tool_handler_result(
    *,
    payload: object | None,
    error: ToolError | None = None,
) -> ToolHandlerResult:
    tool_outcome: ToolOutcome = {
        "payload": payload,
        "error": error,
    }
    return {
        "kind": "tool_result",
        "tool_outcome": tool_outcome,
    }
