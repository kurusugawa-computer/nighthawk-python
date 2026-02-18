from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from ..runtime.step_context import StepContext
from .assignment import assign_tool, eval_expression
from .contracts import ToolBoundaryFailure


@dataclass(frozen=True)
class ProvidedToolDefinition:
    name: str
    tool: Tool[StepContext]


def build_provided_tool_definitions() -> list[ProvidedToolDefinition]:
    metadata = {"nighthawk.provided": True}

    def nh_assign(
        run_context: RunContext[StepContext],
        target_path: str,
        expression: str,
    ) -> dict[str, Any]:
        return assign_tool(
            run_context.deps,
            target_path,
            expression,
        )

    def nh_eval(run_context: RunContext[StepContext], expression: str) -> object:
        try:
            return eval_expression(run_context.deps, expression)
        except Exception as exception:
            raise ToolBoundaryFailure(kind="execution", message=str(exception), guidance="Fix the expression and retry.")

    def nh_dir(run_context: RunContext[StepContext], expression: str) -> str:
        try:
            value = eval_expression(run_context.deps, expression)
            return "\n".join(builtins.dir(value))
        except Exception as exception:
            raise ToolBoundaryFailure(kind="execution", message=str(exception), guidance="Fix the expression and retry.")

    def nh_help(run_context: RunContext[StepContext], expression: str) -> str:
        try:
            value = eval_expression(run_context.deps, expression)
            import pydoc

            return pydoc.render_doc(value)
        except Exception as exception:
            raise ToolBoundaryFailure(kind="execution", message=str(exception), guidance="Fix the expression and retry.")

    return [
        ProvidedToolDefinition(
            name="nh_assign",
            tool=cast(
                Tool[StepContext],
                Tool(
                    nh_assign,
                    name="nh_assign",
                    metadata=metadata,
                    description=("Assign a computed value to a target in the form name(.field)*. On success, returns a JSON-serializable payload with an `updates` list."),
                ),
            ),
        ),
        ProvidedToolDefinition(
            name="nh_eval",
            tool=cast(
                Tool[StepContext],
                Tool(
                    nh_eval,
                    name="nh_eval",
                    metadata=metadata,
                    description=("Evaluate a Python expression and return a JSON-serializable value. Use this to inspect values and call functions."),
                ),
            ),
        ),
        ProvidedToolDefinition(
            name="nh_dir",
            tool=cast(
                Tool[StepContext],
                Tool(
                    nh_dir,
                    name="nh_dir",
                    metadata=metadata,
                    description=("List available attributes on the evaluated value. Use this to explore Python objects safely without mutating state."),
                ),
            ),
        ),
        ProvidedToolDefinition(
            name="nh_help",
            tool=cast(
                Tool[StepContext],
                Tool(
                    nh_help,
                    name="nh_help",
                    metadata=metadata,
                    description=("Return Python help() text for the evaluated value. Use this to read documentation for objects."),
                ),
            ),
        ),
    ]
