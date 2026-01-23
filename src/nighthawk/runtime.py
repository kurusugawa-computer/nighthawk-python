from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from types import FrameType
from typing import Literal

from pydantic import BaseModel, TypeAdapter
from pydantic_ai.toolsets.function import FunctionToolset

from .core import Configuration, Environment, NaturalExecutionError, get_environment
from .llm import NaturalFinal
from .tools import ToolContext, get_visible_tools


def evaluate_template(text: str, template_locals: dict[str, object]) -> str:
    """Evaluate a Python 3.14 template string from trusted input.

    This intentionally allows function execution inside templates under the trusted-input model.
    """

    try:
        template_object = eval("t" + repr(text), {"__builtins__": __builtins__}, template_locals)
    except Exception as e:
        raise NaturalExecutionError(f"Template evaluation failed: {e}") from e

    try:
        strings = template_object.strings
        values = template_object.values
    except Exception as e:
        raise NaturalExecutionError(f"Unexpected template object: {e}") from e

    out: list[str] = []
    for i, s in enumerate(strings):
        out.append(s)
        if i < len(values):
            out.append(str(values[i]))
    return "".join(out)


@dataclass
class Runtime:
    configuration: Configuration
    memory: BaseModel | None

    @classmethod
    def from_environment(cls, environment: Environment) -> "Runtime":
        return cls(
            configuration=environment.configuration,
            memory=environment.memory,
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

    def run_block(
        self,
        natural_program: str,
        binding_names: list[str],
        return_annotation: object,
        is_in_loop: bool,
        *,
        caller_frame: FrameType,
    ) -> dict[str, object]:
        python_locals = caller_frame.f_locals

        processed = evaluate_template(natural_program, python_locals)

        context_globals: dict[str, object] = {"__builtins__": __builtins__}
        context_locals: dict[str, object] = dict(python_locals)
        if self.memory is not None:
            context_locals["memory"] = self.memory

        allowed = set(binding_names)
        tool_context = ToolContext(
            context_globals=context_globals,
            context_locals=context_locals,
            allowed_binding_targets=allowed,
            memory=self.memory,
        )

        final: object
        effect_value: object | None = None

        environment = get_environment()
        if environment.natural_backend == "stub":
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
            if "bindings" not in data:
                raise NaturalExecutionError("Stub Natural execution expected 'bindings' in envelope")

            try:
                final = NaturalFinal.model_validate(data["natural_final"])
            except Exception as e:
                raise NaturalExecutionError(f"Stub Natural execution has invalid natural_final: {e}") from e

            bindings_obj = data["bindings"]
            if not isinstance(bindings_obj, dict):
                raise NaturalExecutionError("Stub Natural execution expected 'bindings' to be an object")

            bindings: dict[str, object] = {}
            for name in binding_names:
                if name in bindings_obj:
                    bindings[name] = bindings_obj[name]

        elif environment.natural_backend == "agent":
            tools = get_visible_tools()
            toolset = FunctionToolset(tools)

            output_type = NaturalFinal
            should_normalize_final = False
            if not is_in_loop:

                class NaturalEffectNoLoop(BaseModel, extra="forbid"):
                    type: Literal["return"]
                    value_json: str | None = None

                class NaturalFinalNoLoop(BaseModel, extra="forbid"):
                    effect: NaturalEffectNoLoop | None = None
                    error: object | None = None

                output_type = NaturalFinalNoLoop
                should_normalize_final = True

            result = environment.agent.run_sync(
                processed,
                deps=tool_context,
                toolsets=[toolset],
                output_type=output_type,
            )
            final = result.output

            error = getattr(final, "error", None)
            if error is not None:
                message = getattr(error, "message", str(error))
                raise NaturalExecutionError(f"Natural execution failed: {message}")

            if should_normalize_final:
                final = NaturalFinal.model_validate(final.model_dump())

            bindings = {}
            for name in binding_names:
                if name in context_locals:
                    bindings[name] = context_locals[name]

        else:
            raise NaturalExecutionError(f"Unknown Natural backend: {environment.natural_backend}")

        final_typed = final
        if not isinstance(final_typed, NaturalFinal):
            final_typed = NaturalFinal.model_validate(final_typed.model_dump())

        effect = final_typed.effect
        if effect is not None:
            if effect.type in ("break", "continue") and not is_in_loop:
                raise NaturalExecutionError(f"Effect '{effect.type}' is only allowed inside loops")
            if effect.type == "return":
                effect_value = self._parse_and_coerce_return_value(effect.value_json, return_annotation)

        return {
            "natural_final": final_typed,
            "bindings": bindings,
            "effect_value": effect_value,
        }


def get_caller_frame() -> FrameType:
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:
        raise NaturalExecutionError("No caller frame")
    return frame.f_back
