from __future__ import annotations

import ast
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from nighthawk.adapters.pydantic_ai.tools.contracts import (
    ToolError,
    ToolHandlerResult,
    ToolOutcome,
)
from nighthawk.core.execution_reference import ExecutionReference
from nighthawk.core.step_context import StepContext
from tests.support.contexts import new_step_context


def _new_step_context() -> StepContext:
    return new_step_context(
        execution_reference=ExecutionReference("test-run", "test-scope", "test_tool_boundary", "test_tool_boundary"),
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        output_binding_names=set(),
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


def parse_tool_error(content: object) -> dict[str, Any]:
    """Read the Python-form error that a tool result reports."""
    assert isinstance(content, str) and content.startswith("error = "), content
    error = ast.literal_eval(content.removeprefix("error = "))
    assert isinstance(error, dict)
    return error
