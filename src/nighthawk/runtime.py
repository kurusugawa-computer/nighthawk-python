from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .configuration import Configuration
from .context import RuntimeContext, get_runtime_context
from .errors import NaturalExecutionError
from .templates import evaluate_template, include
from .tools import ToolContext, assign_tool, dir_tool, eval_tool, help_tool


@dataclass
class Runtime:
    configuration: Configuration
    memory: BaseModel | None

    @classmethod
    def from_runtime_context(cls, ctx: RuntimeContext) -> "Runtime":
        return cls(
            configuration=ctx.configuration,
            memory=ctx.memory,
        )

    def run_block(self, natural_program: str, output_names: list[str]) -> dict[str, object]:
        frame = inspect.currentframe()
        if frame is None or frame.f_back is None:
            raise NaturalExecutionError("No caller frame")

        caller = frame.f_back
        if caller.f_globals.get("__name__") == "nighthawk.decorator" and caller.f_code.co_name == "run_block" and caller.f_back is not None:
            caller = caller.f_back

        python_locals = caller.f_locals

        ctx = get_runtime_context()
        workspace_root = ctx.workspace_root

        template_locals: dict[str, object] = {
            **python_locals,
            "include": lambda p: include(
                p,
                workspace_root=workspace_root,
            ),
        }
        processed = evaluate_template(natural_program, template_locals)

        context_globals: dict[str, object] = {"__builtins__": __builtins__}
        context_locals: dict[str, object] = dict(python_locals)
        if self.memory is not None:
            context_locals["memory"] = self.memory

        allowed = set(output_names)
        tool_ctx = ToolContext(
            context_globals=context_globals,
            context_locals=context_locals,
            allowed_local_targets=allowed,
            memory=self.memory,
        )

        # Stub: interpret the template output as JSON describing assignments.
        # Real LLM integration will replace this.
        json_start = processed.find("{")
        if json_start == -1:
            raise NaturalExecutionError("Natural execution expected JSON object in stub mode")
        try:
            data = json.loads(processed[json_start:])
        except json.JSONDecodeError as e:
            raise NaturalExecutionError(f"Natural execution expected JSON (stub mode): {e}") from e

        if not isinstance(data, dict) or "assignments" not in data:
            raise NaturalExecutionError("Natural inline execution expected {'assignments': [...]} in stub mode")

        assignments = data["assignments"]
        if not isinstance(assignments, list):
            raise NaturalExecutionError("assignments must be a list")

        type_hints: dict[str, Any] = {}
        for item in assignments:
            if not isinstance(item, dict):
                continue
            target = item.get("target")
            expr = item.get("expression")
            if not isinstance(target, str) or not isinstance(expr, str):
                continue
            # Minimal/obvious checks first; then Pydantic validation in assign_tool.
            assign_tool(tool_ctx, target, expr, type_hints=type_hints)

        outputs: dict[str, object] = {}
        for name in output_names:
            if name in context_locals:
                outputs[name] = context_locals[name]
        return outputs

    def tools_for_llm(self) -> dict[str, Any]:
        # Placeholder used later by OpenAI integration.
        return {
            "dir": dir_tool,
            "help": help_tool,
            "eval": eval_tool,
            "assign": assign_tool,
        }
