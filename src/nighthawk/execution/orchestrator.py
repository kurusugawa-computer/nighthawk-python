from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from types import FrameType
from typing import cast

from pydantic import BaseModel, TypeAdapter

from ..errors import ExecutionError
from .context import ExecutionContext, get_execution_context_stack
from .environment import ExecutionEnvironment
from .llm import ExecutionFinal


def evaluate_template(text: str, template_locals: dict[str, object]) -> str:
    """Evaluate a Python 3.14 template string from trusted input.

    This intentionally allows function execution inside templates under the trusted-input model.
    """

    try:
        template_object = eval("t" + repr(text), {"__builtins__": __builtins__}, template_locals)
    except Exception as e:
        raise ExecutionError(f"Template evaluation failed: {e}") from e

    try:
        strings = template_object.strings
        values = template_object.values
    except Exception as e:
        raise ExecutionError(f"Unexpected template object: {e}") from e

    out: list[str] = []
    for i, s in enumerate(strings):
        out.append(s)
        if i < len(values):
            out.append(str(values[i]))
    return "".join(out)


@dataclass
class Orchestrator:
    environment: ExecutionEnvironment

    @classmethod
    def from_environment(cls, environment: ExecutionEnvironment) -> "Orchestrator":
        return cls(environment=environment)

    def _parse_and_coerce_return_value(self, value_json: str | None, return_annotation: object) -> object:
        if value_json is None:
            parsed = None
        else:
            try:
                parsed = json.loads(value_json)
            except json.JSONDecodeError as e:
                raise ExecutionError(f"Invalid return value_json: {e}") from e

        try:
            adapted = TypeAdapter(return_annotation)
            return adapted.validate_python(parsed)
        except Exception as e:
            raise ExecutionError(f"Return value validation failed: {e}") from e

    def run_natural_block(
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

        execution_globals: dict[str, object] = {"__builtins__": __builtins__}

        execution_locals: dict[str, object] = {}

        execution_context_stack = get_execution_context_stack()
        if execution_context_stack:
            execution_locals.update(execution_context_stack[-1].locals)

        execution_locals.update(python_locals)

        if self.environment.memory is not None:
            execution_locals["memory"] = self.environment.memory

        binding_commit_targets = set(binding_names)
        execution_context = ExecutionContext(
            globals=execution_globals,
            locals=execution_locals,
            binding_commit_targets=binding_commit_targets,
            memory=self.environment.memory,
        )

        final: object
        effect_value: object | None = None

        final, bindings = self.environment.execution_executor.run_natural_block(
            processed_natural_program=processed,
            execution_context=execution_context,
            binding_names=binding_names,
            is_in_loop=is_in_loop,
        )

        final_typed = final
        if not isinstance(final_typed, ExecutionFinal):
            if isinstance(final_typed, BaseModel):
                final_typed = ExecutionFinal.model_validate(final_typed.model_dump())
            else:
                final_typed = ExecutionFinal.model_validate(final_typed)

        if not isinstance(final_typed, ExecutionFinal):
            raise ExecutionError("Execution produced unexpected final type")

        final_typed_execution = cast(ExecutionFinal, final_typed)

        effect = final_typed_execution.effect
        if effect is not None:
            if effect.type in ("break", "continue") and not is_in_loop:
                raise ExecutionError(f"Effect '{effect.type}' is only allowed inside loops")
            if effect.type == "return":
                effect_value = self._parse_and_coerce_return_value(effect.value_json, return_annotation)

        return {
            "execution_final": final_typed,
            "bindings": bindings,
            "effect_value": effect_value,
        }


def get_caller_frame() -> FrameType:
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:
        raise ExecutionError("No caller frame")
    return frame.f_back
