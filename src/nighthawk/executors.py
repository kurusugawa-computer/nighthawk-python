from __future__ import annotations

import json
from dataclasses import dataclass
from string import Template
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


def _approx_max_chars_from_tokens(max_tokens: int) -> int:
    return max_tokens * 4


def _summarize_for_prompt(value: object, *, max_chars: int) -> str:
    text = repr(value)
    if len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


def _should_mask_name(name: str, *, name_substrings_to_mask: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(substring in lowered for substring in name_substrings_to_mask)


def _render_locals_section(execution_context: ExecutionContext) -> tuple[str, str]:
    from .core import get_environment

    configuration = get_environment().configuration
    context_limits = configuration.execution.context_limits
    redaction = configuration.execution.context_redaction

    value_max_chars = _approx_max_chars_from_tokens(context_limits.value_max_tokens)
    section_max_chars = _approx_max_chars_from_tokens(context_limits.locals_max_tokens)

    allowed_names = set(redaction.locals_allowlist) if redaction.locals_allowlist else None

    lines: list[str] = []
    total_chars = 0
    shown_items = 0

    eligible_names: list[str] = []
    for name in sorted(execution_context.locals.keys()):
        if name.startswith("__"):
            continue
        if name == "memory":
            continue
        if allowed_names is not None and name not in allowed_names:
            continue
        eligible_names.append(name)

    for name in eligible_names:
        if shown_items >= context_limits.max_items:
            break

        if _should_mask_name(name, name_substrings_to_mask=redaction.name_substrings_to_mask):
            rendered = f"{name} = {redaction.masked_value_marker}"
        else:
            try:
                value = execution_context.locals[name]
            except Exception:
                continue
            rendered_value = _summarize_for_prompt(value, max_chars=value_max_chars)
            rendered = f"{name} = {rendered_value}"

        rendered_with_newline = rendered + "\n"
        if total_chars + len(rendered_with_newline) > section_max_chars:
            break

        lines.append(rendered)
        total_chars += len(rendered_with_newline)
        shown_items += 1

    truncated = shown_items < len(eligible_names)
    if truncated:
        lines.append("...<truncated>")

    return "\n".join(lines), ""  # digest is empty in v1


def _render_memory_section(execution_context: ExecutionContext) -> str:
    from .core import get_environment

    configuration = get_environment().configuration
    context_limits = configuration.execution.context_limits
    redaction = configuration.execution.context_redaction

    memory = execution_context.memory
    if memory is None:
        return ""

    section_max_chars = _approx_max_chars_from_tokens(context_limits.memory_max_tokens)

    allowed_fields = set(redaction.memory_fields_allowlist) if redaction.memory_fields_allowlist else None

    memory_object: dict[str, object]
    try:
        memory_object = dict(memory.model_dump())
    except Exception:
        memory_object = {"__type__": type(memory).__name__, "__repr__": repr(memory)}

    masked: dict[str, object] = {}
    for field_name, field_value in memory_object.items():
        if allowed_fields is not None and field_name not in allowed_fields:
            continue
        if _should_mask_name(field_name, name_substrings_to_mask=redaction.name_substrings_to_mask):
            masked[field_name] = redaction.masked_value_marker
        else:
            masked[field_name] = field_value

    try:
        memory_json = json.dumps(masked, sort_keys=True, ensure_ascii=False)
    except Exception:
        memory_json = json.dumps(repr(masked), ensure_ascii=False)

    if len(memory_json) > section_max_chars:
        memory_json = memory_json[:section_max_chars] + "...<truncated>"

    return memory_json


def build_user_prompt(*, processed_natural_program: str, execution_context: ExecutionContext) -> str:
    from .core import get_environment

    configuration = get_environment().configuration
    template_text = configuration.prompts.natural_block_execution_user_prompt_template

    locals_text, locals_digest_text = _render_locals_section(execution_context)
    memory_text = _render_memory_section(execution_context)

    template = Template(template_text)
    return template.substitute(
        program=processed_natural_program,
        locals_digest=locals_digest_text,
        locals=locals_text,
        memory=memory_text,
    )


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

        user_prompt = build_user_prompt(
            processed_natural_program=processed_natural_program,
            execution_context=execution_context,
        )

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
                user_prompt,
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
