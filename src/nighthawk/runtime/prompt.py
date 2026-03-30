from __future__ import annotations

import functools
import inspect
import logging
import re
from collections.abc import Iterable
from string import Template
from typing import Any, TypeAliasType

import tiktoken

from ..configuration import StepContextLimits, StepExecutorConfiguration
from ..json_renderer import JsonRendererStyle, count_tokens, render_json_text
from .scoping import (
    RUN_ID,
    SCOPE_ID,
    STEP_ID,
    get_execution_context,
    get_user_prompt_suffix_fragments,
)
from .step_context import _MISSING, StepContext, resolve_name_in_step_context


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
        call_attribute = getattr(value, "__call__", None)  # noqa: B004
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
        call_attribute = getattr(value, "__call__", None)  # noqa: B004
        if call_attribute is not None and inspect.iscoroutinefunction(call_attribute):
            return True

    return False


def _find_ambiguous_callable_signatures(
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

    return {
        callable_signature_text: reference_list
        for callable_signature_text, reference_list in callable_signature_text_to_reference_list.items()
        if len(reference_list) > 1
    }


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
            rendered_text += f"  # {usage_intent_hint}"
        return rendered_text

    rendered_text = f"{reference}: {callable_signature_text}"
    metadata_comment_list: list[str] = []
    if usage_intent_hint is not None:
        metadata_comment_list.append(usage_intent_hint)
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
    callable_signature_text_to_reference_list = _find_ambiguous_callable_signatures(reference_and_value_list=reference_and_value_list)

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


def extract_references_and_program(text: str) -> tuple[tuple[str, ...], str]:
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
        if value is _MISSING:
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

    references, program_text = extract_references_and_program(processed_natural_program)
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
