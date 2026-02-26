from __future__ import annotations

import re
from dataclasses import dataclass
from inspect import signature
from string import Template
from typing import Any, Awaitable, Callable, Iterable, Protocol, cast

import tiktoken
from pydantic import TypeAdapter
from pydantic_ai import Agent, StructuredDict
from pydantic_ai.toolsets.function import FunctionToolset

from ..configuration import RunConfiguration
from ..errors import ExecutionError
from ..json_renderer import RenderStyle, count_tokens, render_json_text
from ..tools.contracts import ToolResultWrapperToolset
from ..tools.registry import get_visible_tools
from .async_bridge import run_coroutine_synchronously
from .scoping import RUN_ID, SCOPE_ID, STEP_ID, get_environment, scope, span
from .step_context import StepContext, resolve_name_in_step_context, step_context_scope
from .step_contract import (
    STEP_KINDS,
    StepKind,
    StepOutcome,
    build_step_json_schema,
    build_step_system_prompt_suffix_fragment,
)


class StepExecutionAgent(Protocol):
    def run_sync(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class StepExecutor(Protocol):
    def run_step(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        raise NotImplementedError


def _render_reference_and_value_list_section(
    *,
    reference_and_value_list: list[tuple[str, object]],
    max_items: int,
    section_max_tokens: int,
    value_max_tokens: int,
    token_encoding: tiktoken.Encoding,
    json_renderer_style: RenderStyle,
) -> str:
    lines: list[str] = []
    total_tokens = 0
    shown_items = 0

    for reference, value in reference_and_value_list:
        if shown_items >= max_items:
            break

        reference_type_name = type(value).__name__
        if reference_type_name == "function":
            reference_type_name = str(signature(value))  # type: ignore
        rendered_name_and_type = f"{reference}: {reference_type_name} = "

        rendered_value, rendered_value_tokens = render_json_text(
            value,
            max_tokens=value_max_tokens,
            encoding=token_encoding,
            style=json_renderer_style,
        )

        rendered = rendered_name_and_type + rendered_value
        rendered_tokens = count_tokens(rendered_name_and_type, token_encoding) + rendered_value_tokens

        if total_tokens + rendered_tokens + 1 > section_max_tokens:
            break

        lines.append(rendered)
        total_tokens += rendered_tokens + 1
        shown_items += 1

    truncated = shown_items < len(reference_and_value_list)
    if truncated:
        lines.append("<snipped>")

    return "\n".join(lines)


def _render_locals_section(step_context: StepContext, references: Iterable[str], token_encoding: tiktoken.Encoding) -> str:
    run_configuration = step_context.run_configuration
    context_limits = run_configuration.context_limits

    eligible_reference_and_value_list: list[tuple[str, object]] = []
    for reference in sorted(references):
        if reference.startswith("__"):
            continue
        eligible_reference_and_value_list.append((reference, step_context.step_locals[reference]))

    return _render_reference_and_value_list_section(
        reference_and_value_list=eligible_reference_and_value_list,
        max_items=context_limits.locals_max_items,
        section_max_tokens=context_limits.locals_max_tokens,
        value_max_tokens=context_limits.value_max_tokens,
        token_encoding=token_encoding,
        json_renderer_style=run_configuration.json_renderer_style,
    )


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


def _render_globals_section(step_context: StepContext, references: Iterable[str], token_encoding: tiktoken.Encoding) -> str:
    run_configuration = step_context.run_configuration
    context_limits = run_configuration.context_limits

    eligible_reference_and_value_list: list[tuple[str, object]] = []
    for reference in sorted(references):
        if reference.startswith("__"):
            continue
        if reference in step_context.step_locals:
            continue
        value = resolve_name_in_step_context(step_context, reference)
        if value is None:
            continue
        eligible_reference_and_value_list.append((reference, value))

    return _render_reference_and_value_list_section(
        reference_and_value_list=eligible_reference_and_value_list,
        max_items=context_limits.globals_max_items,
        section_max_tokens=context_limits.globals_max_tokens,
        value_max_tokens=context_limits.value_max_tokens,
        token_encoding=token_encoding,
        json_renderer_style=run_configuration.json_renderer_style,
    )


def build_user_prompt(processed_natural_program: str, step_context: StepContext) -> str:
    run_configuration = step_context.run_configuration
    template_text = run_configuration.prompts.step_user_prompt_template
    token_encoding = tiktoken.get_encoding(run_configuration.tokenizer_encoding)

    locals_text = _render_locals_section(step_context, step_context.step_locals.keys(), token_encoding)

    references, program_text = _extract_references_and_program(processed_natural_program)
    globals_text = _render_globals_section(step_context, references, token_encoding)

    template = Template(template_text)
    current_environment = get_environment()

    prompt_text = template.substitute(
        program=program_text,
        locals=locals_text,
        globals=globals_text,
    )

    suffix_fragments = current_environment.user_prompt_suffix_fragments
    if suffix_fragments:
        return "\n\n".join([prompt_text, *suffix_fragments])

    return prompt_text


def _new_agent_step_executor(
    run_configuration: RunConfiguration,
    agent_constructor_keyword_arguments: dict[str, Any],
) -> StepExecutionAgent:
    model_identifier = run_configuration.model
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
        output_type=StepOutcome,  # pyright: ignore
        deps_type=StepContext,
        system_prompt=run_configuration.prompts.step_system_prompt_template,
        **agent_constructor_keyword_arguments,
    )

    @agent.system_prompt(dynamic=True)
    def _environment_system_prompt() -> str | None:  # pyright: ignore[reportUnusedFunction]
        try:
            current_environment = get_environment()
        except Exception:
            return None

        suffix_fragments = current_environment.system_prompt_suffix_fragments
        if not suffix_fragments:
            return None

        return "\n\n".join(suffix_fragments)

    return agent


@dataclass(frozen=True, init=False)
class AgentStepExecutor:
    agent: StepExecutionAgent

    def __init__(
        self,
        *,
        agent: StepExecutionAgent | None = None,
        run_configuration: RunConfiguration | None = None,
        **agent_constructor_keyword_arguments: Any,
    ) -> None:
        if agent is not None:
            if run_configuration is not None or agent_constructor_keyword_arguments:
                raise ValueError("When agent is provided, do not also pass run_configuration or Agent constructor arguments")
            object.__setattr__(self, "agent", agent)
            return

        if run_configuration is None:
            raise ValueError("AgentStepExecutor requires either agent=... or run_configuration=...")

        object.__setattr__(self, "agent", _new_agent_step_executor(run_configuration, agent_constructor_keyword_arguments))

    async def _run_agent(
        self,
        *,
        user_prompt: str,
        step_context: StepContext,
        toolset: ToolResultWrapperToolset,
        structured_output_type: object,
    ) -> Any:
        async_run_method = getattr(self.agent, "run", None)
        if callable(async_run_method):
            async_run_method_typed = cast(Callable[..., Awaitable[Any]], async_run_method)
            return await async_run_method_typed(
                user_prompt,
                deps=step_context,
                toolsets=[toolset],
                output_type=structured_output_type,
            )

        sync_run_method = getattr(self.agent, "run_sync", None)
        if callable(sync_run_method):
            return sync_run_method(
                user_prompt,
                deps=step_context,
                toolsets=[toolset],
                output_type=structured_output_type,
            )

        raise ExecutionError("AgentStepExecutor requires an agent with run(...) or run_sync(...)")

    async def run_step_async(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        user_prompt = build_user_prompt(
            processed_natural_program=processed_natural_program,
            step_context=step_context,
        )

        tools = get_visible_tools()
        toolset = ToolResultWrapperToolset(FunctionToolset(tools))

        unknown_kinds = set(allowed_step_kinds).difference(STEP_KINDS)
        if unknown_kinds:
            raise ExecutionError(f"Internal error: allowed_step_kinds contains unknown kinds: {tuple(sorted(unknown_kinds))}")

        allowed_kinds_deduplicated = tuple(dict.fromkeys(allowed_step_kinds))
        allowed_kinds_typed = cast(tuple[StepKind, ...], allowed_kinds_deduplicated)

        referenced_names, _ = _extract_references_and_program(processed_natural_program)

        error_type_candidate_names: set[str] = set(referenced_names)
        for name, value in step_context.step_locals.items():
            if isinstance(value, type) and issubclass(value, BaseException) and value.__name__ == name:
                error_type_candidate_names.add(name)

        error_type_binding_name_list: list[str] = []
        for name in sorted(error_type_candidate_names):
            value = resolve_name_in_step_context(step_context, name)
            if value is None:
                continue
            if not isinstance(value, type) or not issubclass(value, BaseException) or value.__name__ != name:
                continue
            error_type_binding_name_list.append(name)

        raise_error_type_binding_names = tuple(error_type_binding_name_list)

        step_system_prompt_fragment = build_step_system_prompt_suffix_fragment(
            allowed_kinds=allowed_kinds_typed,
            raise_error_type_binding_names=raise_error_type_binding_names,
        )

        with scope(system_prompt_suffix_fragment=step_system_prompt_fragment):
            outcome_json_schema = build_step_json_schema(
                allowed_kinds=allowed_kinds_typed,
                raise_error_type_binding_names=raise_error_type_binding_names,
            )
            structured_output_type = StructuredDict(outcome_json_schema, name="StepOutcome")

            current_environment = get_environment()
            with span(
                "nighthawk.step_executor",
                **{
                    RUN_ID: current_environment.run_id,
                    SCOPE_ID: current_environment.scope_id,
                    STEP_ID: step_context.step_id,
                },
            ):
                with step_context_scope(step_context):
                    result = await self._run_agent(
                        user_prompt=user_prompt,
                        step_context=step_context,
                        toolset=toolset,
                        structured_output_type=structured_output_type,
                    )

        try:
            step_outcome = TypeAdapter(StepOutcome).validate_python(result.output)
        except Exception as e:
            raise ExecutionError(f"Step produced invalid step outcome: {e}") from e

        bindings: dict[str, object] = {}
        for name in binding_names:
            if name in step_context.assigned_binding_names:
                bindings[name] = step_context.step_locals[name]

        return step_outcome, bindings

    def run_step(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        return cast(
            tuple[StepOutcome, dict[str, object]],
            run_coroutine_synchronously(
                lambda: self.run_step_async(
                    processed_natural_program=processed_natural_program,
                    step_context=step_context,
                    binding_names=binding_names,
                    allowed_step_kinds=allowed_step_kinds,
                )
            ),
        )
