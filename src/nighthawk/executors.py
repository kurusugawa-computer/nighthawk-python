from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic_ai.toolsets.function import FunctionToolset

from .context import ExecutionContext, execution_context_scope
from .llm import NaturalFinal
from .tools import get_visible_tools


class NaturalAgent(Protocol):
    def run_sync(self, user_prompt: str, /, *, deps: Any = None, toolsets: Any = None, output_type: Any = None, **kwargs: Any) -> Any:
        raise NotImplementedError


class NaturalExecutor(Protocol):
    def run_natural_block(
        self,
        *,
        processed_natural_program: str,
        execution_context: ExecutionContext,
        binding_names: list[str],
        is_in_loop: bool,
    ) -> tuple[NaturalFinal, dict[str, object]]:
        raise NotImplementedError


def _summarize_for_prompt(value: object) -> str:
    text = repr(value)
    if len(text) > 200:
        return text[:200] + "..."
    return text


def build_locals_summary(*, execution_locals: dict[str, object], memory: BaseModel | None) -> str:
    lines: list[str] = []
    lines.append("[nighthawk.locals_summary]")

    for name in sorted(execution_locals.keys()):
        if name.startswith("__"):
            continue
        try:
            value = execution_locals[name]
        except Exception:
            continue
        lines.append(f"{name} = {_summarize_for_prompt(value)}")

    if memory is not None:
        try:
            memory_json = memory.model_dump_json(indent=2)
        except Exception:
            memory_json = _summarize_for_prompt(memory)
        lines.append("[nighthawk.memory_summary]")
        lines.append(memory_json)

    return "\n".join(lines) + "\n\n"


@dataclass(frozen=True)
class StubExecutor:
    def run_natural_block(
        self,
        *,
        processed_natural_program: str,
        execution_context: ExecutionContext,
        binding_names: list[str],
        is_in_loop: bool,
    ) -> tuple[NaturalFinal, dict[str, object]]:
        _ = execution_context
        _ = is_in_loop

        from .core import NaturalExecutionError

        json_start = processed_natural_program.find("{")
        if json_start == -1:
            raise NaturalExecutionError("Natural execution expected JSON object in stub mode")

        try:
            data = json.loads(processed_natural_program[json_start:])
        except json.JSONDecodeError as e:
            raise NaturalExecutionError(f"Natural execution expected JSON (stub mode): {e}") from e

        if not isinstance(data, dict):
            raise NaturalExecutionError("Natural execution expected JSON object (stub mode)")

        if "natural_final" not in data:
            raise NaturalExecutionError("Stub Natural execution expected 'natural_final' in envelope")
        if "bindings" not in data:
            raise NaturalExecutionError("Stub Natural execution expected 'bindings' in envelope")

        try:
            natural_final = NaturalFinal.model_validate(data["natural_final"])
        except Exception as e:
            raise NaturalExecutionError(f"Stub Natural execution has invalid natural_final: {e}") from e

        bindings_object = data["bindings"]
        if not isinstance(bindings_object, dict):
            raise NaturalExecutionError("Stub Natural execution expected 'bindings' to be an object")

        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in bindings_object:
                bindings[name] = bindings_object[name]

        return natural_final, bindings


@dataclass(frozen=True)
class AgentExecutor:
    agent: NaturalAgent

    def run_natural_block(
        self,
        *,
        processed_natural_program: str,
        execution_context: ExecutionContext,
        binding_names: list[str],
        is_in_loop: bool,
    ) -> tuple[NaturalFinal, dict[str, object]]:
        from typing import Literal

        from .core import NaturalExecutionError

        processed_with_summary = build_locals_summary(execution_locals=execution_context.locals, memory=execution_context.memory) + processed_natural_program

        tools = get_visible_tools()
        toolset = FunctionToolset(tools)

        output_type: object = NaturalFinal
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

        with execution_context_scope(execution_context):
            result = self.agent.run_sync(
                processed_with_summary,
                deps=execution_context,
                toolsets=[toolset],
                output_type=output_type,
            )

        final: object = result.output

        error = getattr(final, "error", None)
        if error is not None:
            message = getattr(error, "message", str(error))
            raise NaturalExecutionError(f"Natural execution failed: {message}")

        if should_normalize_final:
            if isinstance(final, BaseModel):
                final = NaturalFinal.model_validate(final.model_dump())
            else:
                final = NaturalFinal.model_validate(final)

        try:
            if isinstance(final, BaseModel):
                final = NaturalFinal.model_validate(final.model_dump())
            else:
                final = NaturalFinal.model_validate(final)
        except Exception as e:
            raise NaturalExecutionError(f"Natural execution produced unexpected final type: {e}") from e

        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in execution_context.locals:
                bindings[name] = execution_context.locals[name]

        return final, bindings
