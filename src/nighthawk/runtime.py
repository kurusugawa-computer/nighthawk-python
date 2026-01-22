from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter

from .configuration import Configuration
from .context import RuntimeContext, get_runtime_context
from .errors import NaturalExecutionError
from .openai_client import NaturalFinal
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

    def _parse_and_coerce_return_value(self, value_json: str | None, return_annotation: object) -> object:
        if value_json is None:
            parsed = None
        else:
            try:
                parsed = json.loads(value_json)
            except json.JSONDecodeError as e:
                raise NaturalExecutionError(f"Invalid return value_json: {e}") from e

        try:
            adapted = TypeAdapter(return_annotation)
            return adapted.validate_python(parsed)
        except Exception as e:
            raise NaturalExecutionError(f"Return value validation failed: {e}") from e

    def run_block(self, natural_program: str, output_names: list[str], return_annotation: object, is_in_loop: bool) -> dict[str, object]:
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

        final: NaturalFinal
        effect_value: object | None = None

        if ctx.natural_backend == "stub":
            json_start = processed.find("{")
            if json_start == -1:
                raise NaturalExecutionError("Natural execution expected JSON object in stub mode")
            try:
                data = json.loads(processed[json_start:])
            except json.JSONDecodeError as e:
                raise NaturalExecutionError(f"Natural execution expected JSON (stub mode): {e}") from e

            if not isinstance(data, dict):
                raise NaturalExecutionError("Natural execution expected JSON object (stub mode)")

            if "natural_final" not in data:
                raise NaturalExecutionError("Stub Natural execution expected 'natural_final' in envelope")
            if "outputs" not in data:
                raise NaturalExecutionError("Stub Natural execution expected 'outputs' in envelope")

            try:
                final = NaturalFinal.model_validate(data["natural_final"])
            except Exception as e:
                raise NaturalExecutionError(f"Stub Natural execution has invalid natural_final: {e}") from e

            outputs_obj = data["outputs"]
            if not isinstance(outputs_obj, dict):
                raise NaturalExecutionError("Stub Natural execution expected 'outputs' to be an object")

            outputs: dict[str, object] = {}
            for name in output_names:
                if name in outputs_obj:
                    outputs[name] = outputs_obj[name]

        elif ctx.natural_backend == "agent":
            result = ctx.agent.run_sync(processed, deps=tool_ctx)
            final = result.output

            error = getattr(final, "error", None)
            if error is not None:
                message = getattr(error, "message", str(error))
                raise NaturalExecutionError(f"Natural execution failed: {message}")

            outputs = {}
            for name in output_names:
                if name in context_locals:
                    outputs[name] = context_locals[name]

        else:
            raise NaturalExecutionError(f"Unknown Natural backend: {ctx.natural_backend}")

        effect = final.effect
        if effect is not None:
            if effect.type in ("break", "continue") and not is_in_loop:
                raise NaturalExecutionError(f"Effect '{effect.type}' is only allowed inside loops")
            if effect.type == "return":
                effect_value = self._parse_and_coerce_return_value(effect.value_json, return_annotation)

        return {
            "natural_final": final,
            "outputs": outputs,
            "effect_value": effect_value,
        }

    def tools_for_llm(self) -> dict[str, Any]:
        # Placeholder used later by OpenAI integration.
        return {
            "dir": dir_tool,
            "help": help_tool,
            "eval": eval_tool,
            "assign": assign_tool,
        }
