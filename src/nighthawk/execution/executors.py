from __future__ import annotations

import json
import re
from dataclasses import dataclass
from inspect import signature
from string import Template
from typing import Any, Iterable, Protocol, cast

import tiktoken
from pydantic import BaseModel, ConfigDict, create_model
from pydantic_ai.toolsets.function import FunctionToolset

from ..configuration import ExecutionConfiguration
from ..json_renderer import SENTINEL_NONSERIALIZABLE, count_tokens, render_json_text, to_jsonable_value
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


def _render_locals_section(execution_context: ExecutionContext, references: Iterable[str], token_encoding: tiktoken.Encoding) -> str:
    execution_configuration = execution_context.execution_configuration
    context_limits = execution_configuration.context_limits
    section_max_tokens = context_limits.locals_max_tokens
    referent_values = execution_context.execution_locals

    def _to_type_name(reference: str) -> str:
        return eval(f"type({reference})", locals=referent_values).__name__

    lines: list[str] = []
    total_tokens = 0
    shown_items = 0

    eligible_references: list[str] = []
    for reference in sorted(references):
        if reference.startswith("__"):
            continue
        eligible_references.append(reference)

    for reference in eligible_references:
        if shown_items >= context_limits.locals_max_items:
            break

        value = referent_values[reference]

        reference_type_name = _to_type_name(reference)
        if reference_type_name == "function":
            reference_type_name = str(signature(value))  # type: ignore
        rendered_name_and_type = f"{reference}: {reference_type_name} = "

        rendered_value, rendered_value_tokens = render_json_text(
            value,
            max_tokens=context_limits.value_max_tokens,
            encoding=token_encoding,
            style=execution_configuration.json_renderer_style,
        )

        rendered = rendered_name_and_type + rendered_value
        rendered_tokens = count_tokens(rendered_name_and_type, token_encoding) + rendered_value_tokens

        if total_tokens + rendered_tokens + 1 > section_max_tokens:
            break

        lines.append(rendered)
        total_tokens += rendered_tokens + 1
        shown_items += 1

    truncated = shown_items < len(eligible_references)
    if truncated:
        lines.append("<snipped>")

    return "\n".join(lines)


def _extract_references_and_program(text: str) -> tuple[tuple[str, ...], str]:
    reference_path_pattern = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"

    unescaped_token_pattern = re.compile(r"(?<!\\)<(" + reference_path_pattern + r")>")
    escaped_token_pattern = re.compile(r"\\<(" + reference_path_pattern + r")>")

    references: set[str] = set()
    for match in unescaped_token_pattern.finditer(text):
        reference_path = match.group(1)
        references.add(reference_path.split(".", 1)[0])

    def unescape(match: re.Match[str]) -> str:
        return f"<{match.group(1)}>"

    unescaped_text = escaped_token_pattern.sub(unescape, text)
    return tuple(references), unescaped_text


def _render_globals_section(execution_context: ExecutionContext, references: Iterable[str], token_encoding: tiktoken.Encoding) -> str:
    execution_configuration = execution_context.execution_configuration
    context_limits = execution_configuration.context_limits
    section_max_tokens = context_limits.globals_max_tokens
    referent_values = execution_context.execution_globals

    def _to_type_name(reference: str) -> str:
        return eval(f"type({reference})", globals=referent_values).__name__

    lines: list[str] = []
    total_tokens = 0
    shown_items = 0

    eligible_references: list[str] = []
    for reference in sorted(references):
        if reference.startswith("__"):
            continue
        if reference in execution_context.execution_locals:
            continue
        if reference not in execution_context.execution_globals:
            continue
        eligible_references.append(reference)

    for reference in eligible_references:
        if shown_items >= context_limits.globals_max_items:
            break

        value = referent_values[reference]

        reference_type_name = _to_type_name(reference)
        if reference_type_name == "function":
            reference_type_name = str(signature(value))  # type: ignore
        rendered_name_and_type = f"{reference}: {reference_type_name} = "

        rendered_value, rendered_value_tokens = render_json_text(
            value,
            max_tokens=context_limits.value_max_tokens,
            encoding=token_encoding,
            style=execution_configuration.json_renderer_style,
        )

        rendered = rendered_name_and_type + rendered_value
        rendered_tokens = count_tokens(rendered_name_and_type, token_encoding) + rendered_value_tokens

        if total_tokens + rendered_tokens + 1 > section_max_tokens:
            break

        lines.append(rendered)
        total_tokens += rendered_tokens + 1
        shown_items += 1

    truncated = shown_items < len(eligible_references)
    if truncated:
        lines.append("<snipped>")

    return "\n".join(lines)


def build_user_prompt(processed_natural_program: str, execution_context: ExecutionContext) -> str:
    execution_configuration = execution_context.execution_configuration
    template_text = execution_configuration.prompts.execution_user_prompt_template
    token_encoding = tiktoken.get_encoding(execution_configuration.tokenizer_encoding)

    locals_text = _render_locals_section(execution_context, execution_context.execution_locals.keys(), token_encoding)

    references, program_text = _extract_references_and_program(processed_natural_program)
    globals_text = _render_globals_section(execution_context, references, token_encoding)

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

        if allowed_effect_types == ():

            class ExecutionFinalNoEffects(BaseModel, extra="forbid"):
                effect: None = None
                error: object | None = None

            output_type = ExecutionFinalNoEffects
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
        else:
            unknown_effect_types = set(allowed_effect_types).difference(EXECUTION_EFFECT_TYPES)
            if unknown_effect_types:
                raise ExecutionError(f"Internal error: allowed_effect_types contains unknown effect types: {tuple(sorted(unknown_effect_types))}")

            allowed_effect_types_deduplicated = tuple(dict.fromkeys(allowed_effect_types))
            allowed_effect_type = cast(Any, Literal)[allowed_effect_types_deduplicated]

            ExecutionEffectWithAllowedSet = create_model(
                "ExecutionEffectWithAllowedSet",
                __config__=ConfigDict(extra="forbid"),
                type=(allowed_effect_type, ...),
                source_path=(str | None, None),
            )

            ExecutionFinalWithAllowedSet = create_model(
                "ExecutionFinalWithAllowedSet",
                __config__=ConfigDict(extra="forbid"),
                effect=(ExecutionEffectWithAllowedSet | None, None),
                error=(object | None, None),
            )

            output_type = ExecutionFinalWithAllowedSet

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

        if isinstance(final, BaseModel):
            final = ExecutionFinal.model_validate(final.model_dump())
        else:
            final = ExecutionFinal.model_validate(final)

        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in execution_context.assigned_binding_names:
                bindings[name] = execution_context.execution_locals[name]

        return final, bindings
