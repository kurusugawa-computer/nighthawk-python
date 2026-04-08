from __future__ import annotations

import dataclasses
import functools
import inspect
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from string import Template
from typing import Any, TypeAliasType

import tiktoken
from pydantic import BaseModel

from ..configuration import StepContextLimits, StepExecutorConfiguration
from ..json_renderer import JsonRendererStyle, count_tokens, render_json_text
from .scoping import (
    RUN_ID,
    SCOPE_ID,
    STEP_ID,
    get_execution_ref,
    get_user_prompt_suffix_fragments,
)
from .step_context import _MISSING, StepContext, resolve_name_in_step_context

type ReferenceAndValue = tuple[str, object]


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
    reference_and_value_list: list[ReferenceAndValue],
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


def _is_object_capability_candidate(value: object) -> bool:
    if callable(value):
        return False
    if isinstance(value, TypeAliasType):
        return False
    if value is None:
        return False
    if isinstance(value, (bool, int, float, str, bytes, bytearray)):
        return False
    return not isinstance(value, (Mapping, Sequence, set, frozenset))


def _resolve_public_method_name_to_value(*, value: object) -> dict[str, object]:
    method_name_to_value: dict[str, object] = {}
    for value_type in type(value).__mro__:
        for method_name, class_attribute in value_type.__dict__.items():
            if method_name.startswith("_"):
                continue
            if method_name in method_name_to_value:
                continue
            if isinstance(class_attribute, property):
                continue

            method_value: object | None = None
            if isinstance(class_attribute, staticmethod):
                method_value = class_attribute.__func__
            elif isinstance(class_attribute, classmethod) or inspect.isfunction(class_attribute):
                method_value = class_attribute.__get__(value, type(value))
            elif callable(class_attribute):
                method_value = class_attribute

            if method_value is None:
                continue

            method_name_to_value[method_name] = method_value

    return {method_name: method_name_to_value[method_name] for method_name in sorted(method_name_to_value)}


def _resolve_public_slot_name_set(*, value: object) -> set[str]:
    public_slot_name_set: set[str] = set()
    for value_type in type(value).__mro__:
        slot_definition = value_type.__dict__.get("__slots__")
        if slot_definition is None:
            continue

        if isinstance(slot_definition, str):
            slot_name_list = [slot_definition]
        else:
            slot_name_list = [slot_name for slot_name in slot_definition if isinstance(slot_name, str)]

        for slot_name in slot_name_list:
            if slot_name.startswith("_"):
                continue
            if slot_name in {"__dict__", "__weakref__"}:
                continue
            public_slot_name_set.add(slot_name)

    return public_slot_name_set


def _resolve_public_field_name_to_value(*, value: object) -> dict[str, object]:
    field_name_to_value: dict[str, object] = {}

    def add_field(field_name: str, field_value: object) -> None:
        if field_name.startswith("_"):
            return
        if field_name in field_name_to_value:
            return
        field_name_to_value[field_name] = field_value

    try:
        instance_dict = object.__getattribute__(value, "__dict__")
    except Exception:
        instance_dict = None

    if isinstance(instance_dict, dict):
        for field_name, field_value in instance_dict.items():
            add_field(field_name, field_value)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for data_field in dataclasses.fields(value):
            field_name = data_field.name
            if field_name in field_name_to_value:
                continue
            try:
                field_value = object.__getattribute__(value, field_name)
            except Exception:
                continue
            add_field(field_name, field_value)

    if isinstance(value, BaseModel):
        for field_name, field_value in value.model_dump(mode="python").items():
            add_field(field_name, field_value)

    for slot_name in sorted(_resolve_public_slot_name_set(value=value)):
        if slot_name in field_name_to_value:
            continue
        try:
            slot_value = object.__getattribute__(value, slot_name)
        except Exception:
            continue
        add_field(slot_name, slot_value)

    for value_type in type(value).__mro__:
        for field_name, class_attribute in value_type.__dict__.items():
            if field_name in field_name_to_value:
                continue
            if field_name.startswith("_"):
                continue
            if isinstance(class_attribute, (property, staticmethod, classmethod)):
                continue
            if callable(class_attribute):
                continue
            add_field(field_name, class_attribute)

    return {field_name: field_name_to_value[field_name] for field_name in sorted(field_name_to_value)}


def _render_value_preview_line(
    *,
    reference: str,
    value: object,
    value_max_tokens: int,
    token_encoding: tiktoken.Encoding,
    json_renderer_style: JsonRendererStyle,
) -> str:
    reference_type_name = type(value).__name__
    rendered_name_and_type = f"{reference}: {reference_type_name} = "

    rendered_value, _ = render_json_text(
        value,
        max_tokens=value_max_tokens,
        encoding=token_encoding,
        style=json_renderer_style,
    )

    return rendered_name_and_type + rendered_value


def _collect_callable_reference_and_value_list(*, reference_and_value_list: list[ReferenceAndValue]) -> list[ReferenceAndValue]:
    callable_reference_and_value_list: list[ReferenceAndValue] = []
    for reference, value in reference_and_value_list:
        if callable(value):
            callable_reference_and_value_list.append((reference, value))
            continue

        if not _is_object_capability_candidate(value):
            continue

        method_name_to_value = _resolve_public_method_name_to_value(value=value)
        for method_name, method_value in method_name_to_value.items():
            callable_reference_and_value_list.append((f"{reference}.{method_name}", method_value))

    return callable_reference_and_value_list


def _render_object_capability_lines(
    *,
    reference: str,
    value: object,
    context_limits: StepContextLimits,
    token_encoding: tiktoken.Encoding,
    json_renderer_style: JsonRendererStyle,
    callable_signature_text_to_reference_list: dict[str, list[str]],
) -> list[str]:
    lines = [f"{reference}: object = {type(value).__name__}"]

    method_name_to_value = _resolve_public_method_name_to_value(value=value)
    field_name_to_value = _resolve_public_field_name_to_value(value=value)

    shown_method_count = 0
    for method_name, method_value in method_name_to_value.items():
        if shown_method_count >= context_limits.object_max_methods:
            break
        method_reference = f"{reference}.{method_name}"
        lines.append(
            _render_callable_line(
                reference=method_reference,
                value=method_value,
                callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
            )
        )
        shown_method_count += 1

    omitted_method_count = len(method_name_to_value) - shown_method_count
    if omitted_method_count > 0:
        lines.append(f"{reference}.<methods>: <snipped {omitted_method_count} public methods>")

    shown_field_count = 0
    for field_name, field_value in field_name_to_value.items():
        if shown_field_count >= context_limits.object_max_fields:
            break
        field_reference = f"{reference}.{field_name}"
        lines.append(
            _render_value_preview_line(
                reference=field_reference,
                value=field_value,
                value_max_tokens=context_limits.object_field_value_max_tokens,
                token_encoding=token_encoding,
                json_renderer_style=json_renderer_style,
            )
        )
        shown_field_count += 1

    omitted_field_count = len(field_name_to_value) - shown_field_count
    if omitted_field_count > 0:
        lines.append(f"{reference}.<fields>: <snipped {omitted_field_count} public fields>")

    return lines


def _render_reference_and_value_lines(
    *,
    reference: str,
    value: object,
    context_limits: StepContextLimits,
    token_encoding: tiktoken.Encoding,
    json_renderer_style: JsonRendererStyle,
    callable_signature_text_to_reference_list: dict[str, list[str]],
) -> list[str]:
    if isinstance(value, TypeAliasType):
        return [f"{reference}: type = {value.__value__}"]

    if callable(value):
        return [
            _render_callable_line(
                reference=reference,
                value=value,
                callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
            )
        ]

    if _is_object_capability_candidate(value):
        return _render_object_capability_lines(
            reference=reference,
            value=value,
            context_limits=context_limits,
            token_encoding=token_encoding,
            json_renderer_style=json_renderer_style,
            callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
        )

    return [
        _render_value_preview_line(
            reference=reference,
            value=value,
            value_max_tokens=context_limits.value_max_tokens,
            token_encoding=token_encoding,
            json_renderer_style=json_renderer_style,
        )
    ]


def _count_rendered_line_list_tokens(*, lines: list[str], token_encoding: tiktoken.Encoding) -> int:
    total_tokens = 0
    for line in lines:
        total_tokens += count_tokens(line, token_encoding) + 1
    return total_tokens


def _render_reference_and_value_list_section(
    *,
    section_name: str,
    step_context: StepContext,
    reference_and_value_list: list[ReferenceAndValue],
    max_items: int,
    section_max_tokens: int,
    context_limits: StepContextLimits,
    token_encoding: tiktoken.Encoding,
    json_renderer_style: JsonRendererStyle,
) -> str:
    lines: list[str] = []
    total_tokens = 0
    shown_items = 0
    token_limit_reached = False

    callable_reference_and_value_list = _collect_callable_reference_and_value_list(reference_and_value_list=reference_and_value_list)
    callable_signature_text_to_reference_list = _find_ambiguous_callable_signatures(reference_and_value_list=callable_reference_and_value_list)

    for reference, value in reference_and_value_list:
        if shown_items >= max_items:
            break

        rendered_line_list = _render_reference_and_value_lines(
            reference=reference,
            value=value,
            context_limits=context_limits,
            token_encoding=token_encoding,
            json_renderer_style=json_renderer_style,
            callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
        )

        rendered_tokens = _count_rendered_line_list_tokens(
            lines=rendered_line_list,
            token_encoding=token_encoding,
        )

        if total_tokens + rendered_tokens > section_max_tokens:
            token_limit_reached = True
            break

        lines.extend(rendered_line_list)
        total_tokens += rendered_tokens
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
                execution_ref = get_execution_ref()
                log_attributes[RUN_ID] = execution_ref.run_id
                log_attributes[SCOPE_ID] = execution_ref.scope_id
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
    eligible_reference_and_value_list: list[ReferenceAndValue] = []
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
        context_limits=context_limits,
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
    eligible_reference_and_value_list: list[ReferenceAndValue] = []
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
        context_limits=context_limits,
        token_encoding=token_encoding,
        json_renderer_style=json_renderer_style,
    )


def _resolve_step_prompt_template_text(
    *,
    template_text: str,
    name_to_value: dict[str, str],
) -> str:
    template = Template(template_text)
    return template.safe_substitute(name_to_value)


def _resolve_step_system_prompt_text(*, configuration: StepExecutorConfiguration) -> str:
    return _resolve_step_prompt_template_text(
        template_text=configuration.prompts.step_system_prompt_template,
        name_to_value={
            "tool_result_max_tokens": str(configuration.context_limits.tool_result_max_tokens),
        },
    )


def _resolve_step_user_prompt_text(
    *,
    template_text: str,
    program_text: str,
    locals_text: str,
    globals_text: str,
    tool_result_max_tokens: int,
) -> str:
    return _resolve_step_prompt_template_text(
        template_text=template_text,
        name_to_value={
            "program": program_text,
            "locals": locals_text,
            "globals": globals_text,
            "tool_result_max_tokens": str(tool_result_max_tokens),
        },
    )


def build_system_prompt(*, configuration: StepExecutorConfiguration) -> str:
    return _resolve_step_system_prompt_text(configuration=configuration)


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
    augmented_global_references = set(references) | set(step_context.implicit_reference_name_to_value.keys())
    globals_text = _render_globals_section(
        step_context=step_context,
        references=augmented_global_references,
        token_encoding=token_encoding,
        context_limits=context_limits,
        json_renderer_style=configuration.json_renderer_style,
    )

    prompt_text = _resolve_step_user_prompt_text(
        template_text=template_text,
        program_text=program_text,
        locals_text=locals_text,
        globals_text=globals_text,
        tool_result_max_tokens=context_limits.tool_result_max_tokens,
    )

    suffix_fragments = (
        *configuration.user_prompt_suffix_fragments,
        *get_user_prompt_suffix_fragments(),
    )
    if suffix_fragments:
        return "\n\n".join([prompt_text, *suffix_fragments])

    return prompt_text
