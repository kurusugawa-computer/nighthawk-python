from __future__ import annotations

import functools
import inspect
import logging
import re
from dataclasses import dataclass, field
from string import Template
from typing import Any, Awaitable, Callable, Iterable, Protocol, TypeAliasType, cast

import tiktoken
from pydantic import TypeAdapter
from pydantic_ai import Agent, StructuredDict
from pydantic_ai.toolsets.function import FunctionToolset

from ..configuration import StepContextLimits, StepExecutorConfiguration
from ..errors import ExecutionError
from ..json_renderer import JsonRendererStyle, count_tokens, render_json_text
from ..tools.contracts import ToolResultWrapperToolset
from ..tools.registry import get_visible_tools
from .async_bridge import run_coroutine_synchronously
from .scoping import (
    RUN_ID,
    SCOPE_ID,
    STEP_ID,
    get_execution_context,
    get_system_prompt_suffix_fragments,
    get_user_prompt_suffix_fragments,
    scope,
    span,
)
from .step_context import StepContext, ToolResultRenderingPolicy, resolve_name_in_step_context, step_context_scope
from .step_contract import (
    STEP_KINDS,
    StepFinalResult,
    StepKind,
    StepOutcome,
    build_step_json_schema,
    build_step_system_prompt_suffix_fragment,
)


class StepExecutionAgent(Protocol):
    def run_sync(self, *args: Any, **kwargs: Any) -> Any: ...


class StepExecutor(Protocol):
    def run_step(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]: ...


def _resolve_partial_effective_signature(partial_callable: functools.partial[Any]) -> str | None:
    try:
        resolved_signature = inspect.signature(partial_callable)
    except (TypeError, ValueError):
        return None
    return str(resolved_signature)


def _resolve_callable_signature_text(value: object) -> str | None:
    if isinstance(value, functools.partial):
        return _resolve_partial_effective_signature(value)

    try:
        resolved_signature = inspect.signature(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return str(resolved_signature)


def _normalize_usage_intent_text(text: str, *, max_length: int = 72) -> str:
    normalized_text = " ".join(text.split())
    if len(normalized_text) <= max_length:
        return normalized_text
    return normalized_text[: max_length - 3].rstrip() + "..."


def _is_meaningful_usage_intent_hint(usage_intent_hint: str) -> bool:
    normalized_lower_text = usage_intent_hint.strip().lower()
    meaningless_usage_intent_hint_set = {
        "call self as a function.",
        "create a new function with partial application of the given arguments and keywords.",
        "create a new function with partial application of the given arguments",
    }
    return normalized_lower_text not in meaningless_usage_intent_hint_set


def _resolve_callable_usage_intent_hint(*, value: object) -> str | None:
    documentation_text: str | None = None

    if isinstance(value, functools.partial):
        documentation_text = inspect.getdoc(value.func)
    elif not inspect.isroutine(value):
        call_attribute = getattr(value, "__call__", None)
        if call_attribute is not None:
            documentation_text = inspect.getdoc(call_attribute)

    if not documentation_text:
        documentation_text = inspect.getdoc(value)

    if documentation_text:
        first_line = documentation_text.splitlines()[0].strip()
        if first_line:
            normalized_usage_intent_hint = _normalize_usage_intent_text(first_line)
            if _is_meaningful_usage_intent_hint(normalized_usage_intent_hint):
                return normalized_usage_intent_hint

    return None


def _is_async_callable_value(value: object) -> bool:
    if isinstance(value, functools.partial):
        return _is_async_callable_value(value.func)

    if inspect.iscoroutinefunction(value):
        return True

    if not inspect.isroutine(value):
        call_attribute = getattr(value, "__call__", None)
        if call_attribute is not None and inspect.iscoroutinefunction(call_attribute):
            return True

    return False


def _build_callable_signature_text_to_reference_list(
    *,
    reference_and_value_list: list[tuple[str, object]],
) -> dict[str, list[str]]:
    callable_signature_text_to_reference_list: dict[str, list[str]] = {}
    for reference, value in reference_and_value_list:
        if not callable(value):
            continue

        callable_signature_text = _resolve_callable_signature_text(value)
        if callable_signature_text is None:
            continue

        callable_signature_text_to_reference_list.setdefault(callable_signature_text, []).append(reference)

    return {callable_signature_text: reference_list for callable_signature_text, reference_list in callable_signature_text_to_reference_list.items() if len(reference_list) > 1}


def _render_callable_line(
    *,
    reference: str,
    value: object,
    callable_signature_text_to_reference_list: dict[str, list[str]],
) -> str:
    usage_intent_hint = _resolve_callable_usage_intent_hint(value=value)
    callable_signature_text = _resolve_callable_signature_text(value)
    if callable_signature_text is None:
        rendered_text = f"{reference}: <callable; signature-unavailable>"
        if usage_intent_hint is not None:
            rendered_text += f"  # intent: {usage_intent_hint}"
        return rendered_text

    rendered_text = f"{reference}: {callable_signature_text}"
    metadata_comment_list: list[str] = []
    if usage_intent_hint is not None:
        metadata_comment_list.append(f"intent: {usage_intent_hint}")
    if _is_async_callable_value(value):
        metadata_comment_list.append("async")
    if callable_signature_text in callable_signature_text_to_reference_list:
        metadata_comment_list.append(f"disambiguation: use {reference}")

    if metadata_comment_list:
        rendered_text += f"  # {'; '.join(metadata_comment_list)}"
    return rendered_text


def _render_reference_and_value_list_section(
    *,
    section_name: str,
    step_context: StepContext,
    reference_and_value_list: list[tuple[str, object]],
    max_items: int,
    section_max_tokens: int,
    value_max_tokens: int,
    token_encoding: tiktoken.Encoding,
    json_renderer_style: JsonRendererStyle,
) -> str:
    lines: list[str] = []
    total_tokens = 0
    shown_items = 0
    token_limit_reached = False
    callable_signature_text_to_reference_list = _build_callable_signature_text_to_reference_list(reference_and_value_list=reference_and_value_list)

    for reference, value in reference_and_value_list:
        if shown_items >= max_items:
            break

        if isinstance(value, TypeAliasType):
            rendered = f"{reference}: type = {value.__value__}"
            rendered_tokens = count_tokens(rendered, token_encoding)
        elif callable(value):
            rendered = _render_callable_line(
                reference=reference,
                value=value,
                callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
            )
            rendered_tokens = count_tokens(rendered, token_encoding)
        else:
            reference_type_name = type(value).__name__
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
            token_limit_reached = True
            break

        lines.append(rendered)
        total_tokens += rendered_tokens + 1
        shown_items += 1

    truncated = shown_items < len(reference_and_value_list)
    if truncated:
        lines.append("<snipped>")
        if token_limit_reached:
            log_attributes: dict[str, Any] = {
                STEP_ID: step_context.step_id,
                "nighthawk.prompt_context.section": section_name,
                "nighthawk.prompt_context.reason": "token_limit",
                "nighthawk.prompt_context.rendered_items": shown_items,
                "nighthawk.prompt_context.total_items": len(reference_and_value_list),
                "nighthawk.prompt_context.max_tokens": section_max_tokens,
            }
            try:
                execution_context = get_execution_context()
                log_attributes[RUN_ID] = execution_context.run_id
                log_attributes[SCOPE_ID] = execution_context.scope_id
            except Exception:
                pass
            logging.getLogger("nighthawk").info("prompt_context_truncated %s", log_attributes)

    return "\n".join(lines)


def _render_locals_section(
    *,
    step_context: StepContext,
    references: Iterable[str],
    token_encoding: tiktoken.Encoding,
    context_limits: StepContextLimits,
    json_renderer_style: JsonRendererStyle,
) -> str:

    eligible_reference_and_value_list: list[tuple[str, object]] = []
    for reference in sorted(references):
        if reference.startswith("__"):
            continue
        eligible_reference_and_value_list.append((reference, step_context.step_locals[reference]))

    return _render_reference_and_value_list_section(
        section_name="locals",
        step_context=step_context,
        reference_and_value_list=eligible_reference_and_value_list,
        max_items=context_limits.locals_max_items,
        section_max_tokens=context_limits.locals_max_tokens,
        value_max_tokens=context_limits.value_max_tokens,
        token_encoding=token_encoding,
        json_renderer_style=json_renderer_style,
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


def _render_globals_section(
    *,
    step_context: StepContext,
    references: Iterable[str],
    token_encoding: tiktoken.Encoding,
    context_limits: StepContextLimits,
    json_renderer_style: JsonRendererStyle,
) -> str:

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
        section_name="globals",
        step_context=step_context,
        reference_and_value_list=eligible_reference_and_value_list,
        max_items=context_limits.globals_max_items,
        section_max_tokens=context_limits.globals_max_tokens,
        value_max_tokens=context_limits.value_max_tokens,
        token_encoding=token_encoding,
        json_renderer_style=json_renderer_style,
    )


def build_user_prompt(
    *,
    processed_natural_program: str,
    step_context: StepContext,
    configuration: StepExecutorConfiguration,
) -> str:
    template_text = configuration.prompts.step_user_prompt_template
    context_limits = configuration.context_limits
    token_encoding = configuration.resolve_token_encoding()

    locals_text = _render_locals_section(
        step_context=step_context,
        references=step_context.step_locals.keys(),
        token_encoding=token_encoding,
        context_limits=context_limits,
        json_renderer_style=configuration.json_renderer_style,
    )

    references, program_text = _extract_references_and_program(processed_natural_program)
    globals_text = _render_globals_section(
        step_context=step_context,
        references=references,
        token_encoding=token_encoding,
        context_limits=context_limits,
        json_renderer_style=configuration.json_renderer_style,
    )

    template = Template(template_text)

    prompt_text = template.substitute(
        program=program_text,
        locals=locals_text,
        globals=globals_text,
    )

    suffix_fragments = (
        *configuration.user_prompt_suffix_fragments,
        *get_user_prompt_suffix_fragments(),
    )
    if suffix_fragments:
        return "\n\n".join([prompt_text, *suffix_fragments])

    return prompt_text


def _new_agent_step_executor(
    configuration: StepExecutorConfiguration,
) -> StepExecutionAgent:
    model_identifier = configuration.model
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

    constructor_arguments: dict[str, Any] = {}
    if configuration.model_settings is not None:
        constructor_arguments["model_settings"] = configuration.model_settings

    agent = Agent(
        model=model,
        output_type=StepFinalResult,
        deps_type=StepContext,
        system_prompt=configuration.prompts.step_system_prompt_template,
        **constructor_arguments,
    )

    @agent.system_prompt(dynamic=True)
    def _system_prompt_suffixes() -> str | None:  # pyright: ignore[reportUnusedFunction]
        suffix_fragments = (
            *configuration.system_prompt_suffix_fragments,
            *get_system_prompt_suffix_fragments(),
        )
        if not suffix_fragments:
            return None
        return "\n\n".join(suffix_fragments)

    return agent


@dataclass(frozen=True)
class AgentStepExecutor:
    """Step executor that delegates Natural block execution to a Pydantic AI agent.

    Attributes:
        configuration: The step executor configuration.
        agent: The underlying agent instance. If not provided, one is created
            from the configuration.
    """

    configuration: StepExecutorConfiguration = field(default_factory=StepExecutorConfiguration)
    agent: StepExecutionAgent | None = None
    token_encoding: tiktoken.Encoding = field(init=False)
    tool_result_rendering_policy: ToolResultRenderingPolicy = field(init=False)
    agent_is_managed: bool = field(init=False)

    def __post_init__(self) -> None:
        resolved_agent = self.agent if self.agent is not None else _new_agent_step_executor(self.configuration)
        agent_is_managed = self.agent is None
        resolved_token_encoding = self.configuration.resolve_token_encoding()
        object.__setattr__(self, "agent", resolved_agent)
        object.__setattr__(self, "agent_is_managed", agent_is_managed)
        object.__setattr__(self, "token_encoding", resolved_token_encoding)
        object.__setattr__(
            self,
            "tool_result_rendering_policy",
            ToolResultRenderingPolicy(
                tokenizer_encoding_name=resolved_token_encoding.name,
                tool_result_max_tokens=self.configuration.context_limits.tool_result_max_tokens,
                json_renderer_style=self.configuration.json_renderer_style,
            ),
        )

    @classmethod
    def from_agent(
        cls,
        *,
        agent: StepExecutionAgent,
        configuration: StepExecutorConfiguration | None = None,
    ) -> AgentStepExecutor:
        """Create an executor wrapping an existing agent.

        Args:
            agent: A pre-configured agent to use for step execution.
            configuration: Optional configuration. Defaults to
                StepExecutorConfiguration().
        """
        resolved_configuration = configuration if configuration is not None else StepExecutorConfiguration()
        return cls(configuration=resolved_configuration, agent=agent)

    @classmethod
    def from_configuration(
        cls,
        *,
        configuration: StepExecutorConfiguration,
    ) -> AgentStepExecutor:
        """Create an executor from a configuration, building a managed agent internally."""
        return cls(configuration=configuration)

    async def _run_agent(
        self,
        *,
        user_prompt: str,
        step_context: StepContext,
        toolset: ToolResultWrapperToolset,
        structured_output_type: object,
    ) -> Any:
        assert self.agent is not None
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
        if step_context.tool_result_rendering_policy is None:
            step_context.tool_result_rendering_policy = self.tool_result_rendering_policy

        user_prompt = build_user_prompt(
            processed_natural_program=processed_natural_program,
            step_context=step_context,
            configuration=self.configuration,
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
            structured_output_type = StructuredDict(outcome_json_schema, name="StepFinalResult")

            execution_context = get_execution_context()
            with span(
                "nighthawk.step_executor",
                **{
                    RUN_ID: execution_context.run_id,
                    SCOPE_ID: execution_context.scope_id,
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
            raw_output = result.output
            if isinstance(raw_output, StepFinalResult):
                step_outcome = raw_output.result
            elif isinstance(raw_output, dict) and "result" in raw_output:
                step_outcome = TypeAdapter(StepOutcome).validate_python(raw_output["result"])
            else:
                step_outcome = TypeAdapter(StepOutcome).validate_python(raw_output)
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
