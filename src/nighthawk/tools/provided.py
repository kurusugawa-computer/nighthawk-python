from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from ..execution.context import ExecutionContext
from .assignment import assign_tool, eval_expression


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
        try:
            return json.dumps(value, default=repr)
        except Exception:
            return json.dumps(repr(value))

    def nh_assign(
        run_context: RunContext[ExecutionContext],
        target: str,
        expression: str,
        type_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return assign_tool(
            run_context.deps,
            target,
            expression,
            type_hints=(type_hints or {}),
        )

    def nh_json_dumps(run_context: RunContext[ExecutionContext], value: object) -> str:
        _ = run_context
        try:
            return json.dumps(value, default=repr)
        except Exception:
            return json.dumps(repr(value))

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
                description=("Assign a computed value into a local target (<name>) or into memory.<field>. Local targets are any ASCII identifier except reserved names (memory and names starting with '__')."),
            ),
        ),
        ProvidedToolDefinition(
            name="nh_json_dumps",
            tool=Tool(
                nh_json_dumps,
                name="nh_json_dumps",
                metadata=metadata,
                description=("Serialize a Python value to JSON text using json.dumps(value, default=repr). This is a pure helper and does not access the interpreter context."),
            ),
        ),
    ]
