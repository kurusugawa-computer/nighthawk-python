from __future__ import annotations

import json
from dataclasses import dataclass
from string import Template
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.toolsets.function import FunctionToolset

from ..configuration import ExecutionConfiguration
from ..tools import get_visible_tools
from .context import ExecutionContext, execution_context_scope
from .llm import EXECUTION_EFFECT_TYPES, ExecutionFinal


class ExecutionAgent(Protocol):
    def run_sync(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class ExecutionExecutor(Protocol):
    def run_natural_block(
        self,
        *,
        processed_natural_program: str,
        execution_context: ExecutionContext,
        binding_names: list[str],
        is_in_loop: bool,
        allowed_effect_types: tuple[str, ...] = EXECUTION_EFFECT_TYPES,
    ) -> tuple[ExecutionFinal, dict[str, object]]:
        raise NotImplementedError


def make_agent_executor(
    execution_configuration: ExecutionConfiguration,
    **agent_constructor_keyword_arguments: Any,
) -> "AgentExecutor":
    agent: ExecutionAgent = Agent(
        model=execution_configuration.model,
        output_type=ExecutionFinal,
        deps_type=ExecutionContext,
        system_prompt=execution_configuration.prompts.execution_system_prompt_template,
        **agent_constructor_keyword_arguments,
    )
    return AgentExecutor(agent)


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


def _render_locals_section(execution_context: ExecutionContext) -> str:
    execution_configuration = execution_context.execution_configuration
    context_limits = execution_configuration.context_limits
    redaction = execution_configuration.context_redaction

    value_max_chars = _approx_max_chars_from_tokens(context_limits.value_max_tokens)
    section_max_chars = _approx_max_chars_from_tokens(context_limits.locals_max_tokens)

    allowed_names = set(redaction.locals_allowlist) if redaction.locals_allowlist else None

    lines: list[str] = []
    total_chars = 0
    shown_items = 0

    eligible_names: list[str] = []
    for name in sorted(execution_context.execution_locals.keys()):
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
                value = execution_context.execution_locals[name]
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

    return "\n".join(lines)


def _render_memory_section(execution_context: ExecutionContext) -> str:
    execution_configuration = execution_context.execution_configuration
    context_limits = execution_configuration.context_limits
    redaction = execution_configuration.context_redaction

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
    execution_configuration = execution_context.execution_configuration
    template_text = execution_configuration.prompts.execution_user_prompt_template

    locals_text = _render_locals_section(execution_context)
    memory_text = _render_memory_section(execution_context)

    template = Template(template_text)
    return template.substitute(
        program=processed_natural_program,
        locals=locals_text,
        memory=memory_text,
    )


@dataclass(frozen=True)
class AgentExecutor:
    agent: ExecutionAgent

    def run_natural_block(
        self,
        *,
        processed_natural_program: str,
        execution_context: ExecutionContext,
        binding_names: list[str],
        is_in_loop: bool,
        allowed_effect_types: tuple[str, ...] = EXECUTION_EFFECT_TYPES,
    ) -> tuple[ExecutionFinal, dict[str, object]]:
        from typing import Literal

        from ..errors import ExecutionError

        user_prompt = build_user_prompt(
            processed_natural_program=processed_natural_program,
            execution_context=execution_context,
        )

        tools = get_visible_tools()
        toolset = FunctionToolset(tools)

        output_type: object
        should_normalize_final = False

        if allowed_effect_types == ():

            class ExecutionFinalNoEffects(BaseModel, extra="forbid"):
                effect: None = None
                error: object | None = None

            output_type = ExecutionFinalNoEffects
            should_normalize_final = True
        elif not is_in_loop:
            if allowed_effect_types != ("return",):
                raise ExecutionError("Internal error: when is_in_loop is false, allowed_effect_types must be () or ('return',)")

            class ExecutionEffectNoLoop(BaseModel, extra="forbid"):
                type: Literal["return"]
                source_path: str | None = None

            class ExecutionFinalNoLoop(BaseModel, extra="forbid"):
                effect: ExecutionEffectNoLoop | None = None
                error: object | None = None

            output_type = ExecutionFinalNoLoop
            should_normalize_final = True
        else:
            literal = Literal[allowed_effect_types]

            class ExecutionEffectWithAllowedSet(BaseModel, extra="forbid"):
                type: literal  # type: ignore[valid-type]
                source_path: str | None = None

            class ExecutionFinalWithAllowedSet(BaseModel, extra="forbid"):
                effect: ExecutionEffectWithAllowedSet | None = None
                error: object | None = None

            output_type = ExecutionFinalWithAllowedSet
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
            raise ExecutionError(f"Execution failed: {message}")

        if should_normalize_final:
            if isinstance(final, BaseModel):
                final = ExecutionFinal.model_validate(final.model_dump())
            else:
                final = ExecutionFinal.model_validate(final)

        try:
            if isinstance(final, BaseModel):
                final = ExecutionFinal.model_validate(final.model_dump())
            else:
                final = ExecutionFinal.model_validate(final)
        except Exception as e:
            raise ExecutionError(f"Execution produced unexpected final type: {e}") from e

        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in execution_context.execution_locals:
                bindings[name] = execution_context.execution_locals[name]

        return final, bindings
