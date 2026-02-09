from __future__ import annotations

import re
from dataclasses import dataclass
from inspect import signature
from string import Template
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic_ai.toolsets.function import FunctionToolset

from ..configuration import ExecutionConfiguration
from ..tools.contracts import ToolResultWrapperToolset
from ..tools.registry import get_visible_tools
from .context import ExecutionContext, execution_context_scope
from .contracts import EXECUTION_EFFECT_TYPES, ExecutionFinal
from .environment import get_environment


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

    lines: list[str] = []
    total_chars = 0
    shown_items = 0

    allowed_names = set(redaction.locals_allowlist) if redaction.locals_allowlist else None

    eligible_names: list[str] = []
    for name in sorted(execution_context.execution_locals.keys()):
        if name.startswith("__"):
            continue
        if allowed_names is not None and name not in allowed_names:
            continue
        eligible_names.append(name)

    for name in eligible_names:
        if shown_items >= context_limits.locals_max_items:
            break

        if _should_mask_name(name, name_substrings_to_mask=redaction.name_substrings_to_mask):
            rendered_value = redaction.masked_value_marker
        else:
            try:
                value = execution_context.execution_locals[name]
            except Exception:
                continue
            rendered_value = _summarize_for_prompt(value, max_chars=value_max_chars)

        name_type = eval(f"type({name})", locals=execution_context.execution_locals).__name__
        if name_type == "function":
            name_type = str(signature(value))  # type: ignore
        rendered = f"{name}: {name_type} = {rendered_value}"

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


def _find_reference_tokens(*, text: str) -> tuple[tuple[str, ...], str]:
    reference_pattern = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"

    unescaped_token_pattern = re.compile(r"(?<!\\)<(" + reference_pattern + r")>")
    escaped_token_pattern = re.compile(r"\\<(" + reference_pattern + r")>")

    selected_references: list[str] = []
    for match in unescaped_token_pattern.finditer(text):
        selected_references.append(match.group(1))

    def unescape(match: re.Match[str]) -> str:
        return f"<{match.group(1)}>"

    unescaped_text = escaped_token_pattern.sub(unescape, text)

    seen: set[str] = set()
    ordered: list[str] = []
    for reference in selected_references:
        if reference in seen:
            continue
        seen.add(reference)
        ordered.append(reference)

    return tuple(ordered), unescaped_text


def _render_globals_section(*, execution_context: ExecutionContext, references_in_appearance_order: tuple[str, ...]) -> str:
    execution_configuration = execution_context.execution_configuration
    context_limits = execution_configuration.context_limits
    redaction = execution_configuration.context_redaction

    value_max_chars = _approx_max_chars_from_tokens(context_limits.value_max_tokens)
    section_max_chars = _approx_max_chars_from_tokens(context_limits.globals_max_tokens)

    lines: list[str] = []
    total_chars = 0
    shown_items = 0
    limit_reached = False

    selected_top_level_name_set: set[str] = set()
    for reference_path in references_in_appearance_order:
        top_level_name = reference_path.split(".", 1)[0]
        if top_level_name.startswith("__"):
            continue
        selected_top_level_name_set.add(top_level_name)

    eligible_top_level_names: list[str] = []
    for top_level_name in sorted(selected_top_level_name_set):
        if top_level_name in execution_context.execution_locals:
            continue
        if top_level_name not in execution_context.execution_globals:
            continue
        eligible_top_level_names.append(top_level_name)

    for top_level_name in eligible_top_level_names:
        if shown_items >= context_limits.globals_max_items:
            limit_reached = True
            break

        value = execution_context.execution_globals[top_level_name]

        if _should_mask_name(top_level_name, name_substrings_to_mask=redaction.name_substrings_to_mask):
            rendered_value = redaction.masked_value_marker
        else:
            rendered_value = _summarize_for_prompt(value, max_chars=value_max_chars)

        name_type = eval(f"type({top_level_name})", globals=execution_context.execution_globals).__name__
        if name_type == "function":
            name_type = str(signature(value))  # type: ignore
        rendered = f"{top_level_name}: {name_type} = {rendered_value}"

        rendered_with_newline = rendered + "\n"
        if total_chars + len(rendered_with_newline) > section_max_chars:
            limit_reached = True
            break

        lines.append(rendered)
        total_chars += len(rendered_with_newline)
        shown_items += 1

    if limit_reached:
        lines.append("...<truncated>")

    return "\n".join(lines)


def build_user_prompt(*, processed_natural_program: str, execution_context: ExecutionContext) -> str:
    execution_configuration = execution_context.execution_configuration
    template_text = execution_configuration.prompts.execution_user_prompt_template

    references, program_text = _find_reference_tokens(text=processed_natural_program)

    locals_text = _render_locals_section(execution_context)
    globals_text = _render_globals_section(execution_context=execution_context, references_in_appearance_order=references)

    template = Template(template_text)
    environment = get_environment()

    prompt_text = template.substitute(
        program=program_text,
        locals=locals_text,
        globals=globals_text,
    )

    suffix_fragments = environment.execution_user_prompt_suffix_fragments
    if suffix_fragments:
        return "\n\n".join([prompt_text, *suffix_fragments])

    return prompt_text


def _new_agent_executor(
    execution_configuration: ExecutionConfiguration,
    agent_constructor_keyword_arguments: dict[str, Any],
) -> ExecutionAgent:
    from pydantic_ai import Agent

    model_identifier = execution_configuration.model
    provider, provider_model_name = model_identifier.split(":", 1)

    match provider:
        case "claude-code":
            from ..backends.claude_code import ClaudeCodeModel

            model: object = ClaudeCodeModel(model_name=provider_model_name if provider_model_name != "default" else None)
        case "codex":
            from ..backends.codex import CodexModel

            model = CodexModel(model_name=provider_model_name if provider_model_name != "default" else None)
        case _:
            model = model_identifier

    agent = Agent(
        model=model,
        output_type=ExecutionFinal,
        deps_type=ExecutionContext,
        system_prompt=execution_configuration.prompts.execution_system_prompt_template,
        **agent_constructor_keyword_arguments,
    )

    @agent.system_prompt(dynamic=True)
    def _environment_system_prompt() -> str | None:
        try:
            environment = get_environment()
        except Exception:
            return None

        suffix_fragments = environment.execution_system_prompt_suffix_fragments
        if not suffix_fragments:
            return None

        return "\n\n".join(suffix_fragments)

    return agent


@dataclass(frozen=True, init=False)
class AgentExecutor:
    agent: ExecutionAgent

    def __init__(
        self,
        *,
        agent: ExecutionAgent | None = None,
        execution_configuration: ExecutionConfiguration | None = None,
        **agent_constructor_keyword_arguments: Any,
    ) -> None:
        if agent is not None:
            if execution_configuration is not None or agent_constructor_keyword_arguments:
                raise ValueError("When agent is provided, do not also pass execution_configuration or Agent constructor arguments")
            object.__setattr__(self, "agent", agent)
            return

        if execution_configuration is None:
            raise ValueError("AgentExecutor requires either agent=... or execution_configuration=...")

        object.__setattr__(self, "agent", _new_agent_executor(execution_configuration, agent_constructor_keyword_arguments))

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
        toolset = ToolResultWrapperToolset(FunctionToolset(tools))

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
            if name in execution_context.assigned_binding_names:
                bindings[name] = execution_context.execution_locals[name]

        return final, bindings
