from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from ..execution.context import ExecutionContext
from .assignment import assign_tool, eval_expression, serialize_value_to_json_text


@dataclass(frozen=True)
class ProvidedToolDefinition:
    name: str
    tool: Tool[ExecutionContext]


def build_provided_tool_definitions() -> list[ProvidedToolDefinition]:
    metadata = {"nighthawk.provided": True}

    def nh_dir(run_context: RunContext[ExecutionContext], expression: str) -> str:
        value = eval_expression(run_context.deps, expression)
        return "\n".join(builtins.dir(value))

    def nh_help(run_context: RunContext[ExecutionContext], expression: str) -> str:
        value = eval_expression(run_context.deps, expression)
        import pydoc

        return pydoc.render_doc(value)

    def nh_eval(run_context: RunContext[ExecutionContext], expression: str) -> str:
        value = eval_expression(run_context.deps, expression)
        return serialize_value_to_json_text(value)

    def nh_assign(
        run_context: RunContext[ExecutionContext],
        target_path: str,
        expression: str,
    ) -> dict[str, Any]:
        return assign_tool(
            run_context.deps,
            target_path,
            expression,
        )

    return [
        ProvidedToolDefinition(
            name="nh_dir",
            tool=Tool(
                nh_dir,
                name="nh_dir",
                metadata=metadata,
                description=("List available attributes on the evaluated value. Use this to explore Python objects safely without mutating state."),
            ),
        ),
        ProvidedToolDefinition(
            name="nh_help",
            tool=Tool(
                nh_help,
                name="nh_help",
                metadata=metadata,
                description=("Return Python help() text for the evaluated value. Use this to read documentation for objects available in context."),
            ),
        ),
        ProvidedToolDefinition(
            name="nh_eval",
            tool=Tool(
                nh_eval,
                name="nh_eval",
                metadata=metadata,
                description=("Evaluate a Python expression in the tool evaluation environment and return JSON text. Use this to inspect values; do not use it to mutate state."),
            ),
        ),
        ProvidedToolDefinition(
            name="nh_assign",
            tool=Tool(
                nh_assign,
                name="nh_assign",
                metadata=metadata,
                description=("Assign a computed value to a target in the form name(.field)*. The tool returns a diagnostic object with an `updates` list on success and never raises."),
            ),
        ),
    ]
