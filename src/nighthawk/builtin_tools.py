from __future__ import annotations

import json
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from .agent import ToolContext, assign_tool, dir_tool, eval_tool, help_tool


def get_builtin_tool_definitions():
    from .tools import ToolDefinition

    metadata = {"nighthawk.builtin": True}

    def nh_dir(run_context: RunContext[ToolContext], expression: str) -> str:
        return dir_tool(run_context.deps, expression)

    def nh_help(run_context: RunContext[ToolContext], expression: str) -> str:
        return help_tool(run_context.deps, expression)

    def nh_eval(run_context: RunContext[ToolContext], expression: str) -> str:
        return eval_tool(run_context.deps, expression)

    def nh_assign(
        run_context: RunContext[ToolContext],
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

    def nh_json_dumps(run_context: RunContext[ToolContext], value: object) -> str:
        _ = run_context
        try:
            return json.dumps(value, default=repr)
        except Exception:
            return json.dumps(repr(value))

    return [
        ToolDefinition(
            name="nh_dir",
            tool=Tool(
                nh_dir,
                name="nh_dir",
                metadata=metadata,
                description=("List available attributes on the evaluated value. Use this to explore Python objects safely without mutating state."),
            ),
        ),
        ToolDefinition(
            name="nh_help",
            tool=Tool(
                nh_help,
                name="nh_help",
                metadata=metadata,
                description=("Return Python help() text for the evaluated value. Use this to read documentation for objects available in context."),
            ),
        ),
        ToolDefinition(
            name="nh_eval",
            tool=Tool(
                nh_eval,
                name="nh_eval",
                metadata=metadata,
                description=("Evaluate a Python expression in the tool evaluation environment and return JSON text. Use this to inspect values; do not use it to mutate state."),
            ),
        ),
        ToolDefinition(
            name="nh_assign",
            tool=Tool(
                nh_assign,
                name="nh_assign",
                metadata=metadata,
                description=("Assign a computed value into a writable binding (<name>) or into memory.<field>. Bindings are restricted to the allowlist derived from <:name> bindings in the current Natural block."),
            ),
        ),
        ToolDefinition(
            name="nh_json_dumps",
            tool=Tool(
                nh_json_dumps,
                name="nh_json_dumps",
                metadata=metadata,
                description=("Serialize a Python value to JSON text using json.dumps(value, default=repr). This is a pure helper and does not access the interpreter context."),
            ),
        ),
    ]
