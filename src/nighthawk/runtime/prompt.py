from __future__ import annotations

import dataclasses
import functools
import inspect
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from string import Template
from typing import Any, NamedTuple, TypeAliasType

import tiktoken
from pydantic import BaseModel
from pydantic_ai.messages import TextContent, UserContent

from ..configuration import StepContextLimits, StepExecutorConfiguration
from ..json_renderer import JsonRendererStyle, count_tokens, render_json_text
from ._user_content import (
    coalesce_user_content,
    try_project_user_prompt_value,
)
from .scoping import (
    RUN_ID,
    SCOPE_ID,
    STEP_ID,
    _current_user_prompt_suffix_fragments,
    get_execution_ref,
)
from .step_context import _MISSING, StepContext, resolve_name_in_step_context

type ReferenceAndValue = tuple[str, object]
type PromptContentTuple = tuple[UserContent, ...]

# Fixed internal budgeting heuristic per spec §8.2.2; not a provider-side estimate.
_MULTIMODAL_BINDING_TOKEN_COST = 64


@dataclass(frozen=True)
class _PromptRenderContext:
    """Stable rendering invariants for one Natural block prompt pass.

    Bundles the per-step rendering configuration that flows unchanged through
    section / reference / object-capability rendering. Per-section values such
    as the callable-signature disambiguation map are *not* included here: they
    are computed inside :func:`_render_reference_and_value_list_section` and
    passed alongside this context to the deeper renderers.
    """

    context_limits: StepContextLimits
    token_encoding: tiktoken.Encoding
    json_renderer_style: JsonRendererStyle
    explicit_dotted_reference_path_set: frozenset[str]


class ExtractedReferenceData(NamedTuple):
    references: tuple[str, ...]
    reference_paths: tuple[str, ...]
    program_text: str


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
        if isinstance(value, BaseModel) and value_type is BaseModel:
            break
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

    if isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            try:
                field_value = object.__getattribute__(value, field_name)
            except Exception:
                continue
            add_field(field_name, field_value)
        return {field_name: field_name_to_value[field_name] for field_name in sorted(field_name_to_value)}

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


def _append_prompt_line(
    *,
    content_part_list: list[UserContent],
    prompt_line: tuple[UserContent, ...],
) -> None:
    if content_part_list:
        content_part_list.append("\n")
    content_part_list.extend(prompt_line)


def _count_prompt_line_tokens(
    *,
    prompt_line: tuple[UserContent, ...],
    token_encoding: tiktoken.Encoding,
) -> int:
    # Spec §8.2.2: line-level token counting includes the newline separator.
    # The fixed +1 charge here represents that line-separator budget.
    total_tokens = 1
    for content in prompt_line:
        if isinstance(content, str):
            total_tokens += count_tokens(content, token_encoding)
        elif isinstance(content, TextContent):
            total_tokens += count_tokens(content.content, token_encoding)
        else:
            total_tokens += _MULTIMODAL_BINDING_TOKEN_COST
    return total_tokens


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
    render_context: _PromptRenderContext,
    callable_signature_text_to_reference_list: dict[str, list[str]],
) -> list[tuple[UserContent, ...]]:
    lines: list[tuple[UserContent, ...]] = [(f"{reference}: object = {type(value).__name__}",)]

    method_name_to_value = _resolve_public_method_name_to_value(value=value)
    field_name_to_value = _resolve_public_field_name_to_value(value=value)

    shown_method_count = 0
    for method_name, method_value in method_name_to_value.items():
        if shown_method_count >= render_context.context_limits.object_max_methods:
            break
        method_reference = f"{reference}.{method_name}"
        lines.append(
            (
                _render_callable_line(
                    reference=method_reference,
                    value=method_value,
                    callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
                ),
            )
        )
        shown_method_count += 1

    omitted_method_count = len(method_name_to_value) - shown_method_count
    if omitted_method_count > 0:
        lines.append((f"{reference}.<methods>: <snipped {omitted_method_count} public methods>",))

    shown_field_count = 0
    for field_name, field_value in field_name_to_value.items():
        if shown_field_count >= render_context.context_limits.object_max_fields:
            break
        field_reference = f"{reference}.{field_name}"
        if field_reference in render_context.explicit_dotted_reference_path_set and try_project_user_prompt_value(field_value) is not None:
            # Explicit dotted multimodal reference (e.g. <holder.photo>) is rendered as its
            # own top-level line outside this object block (spec §8.2.2). Skip inline
            # rendering here to avoid duplication, but still consume one object_max_fields
            # slot so truncation math stays stable regardless of hoisting.
            # Spec §8.2.2: lex-order truncation deliberately covers explicit
            # dotted multimodal leaves; do not reorder or force them past budgets.
            shown_field_count += 1
            continue
        lines.append(
            (
                _render_value_preview_line(
                    reference=field_reference,
                    value=field_value,
                    value_max_tokens=render_context.context_limits.object_field_value_max_tokens,
                    token_encoding=render_context.token_encoding,
                    json_renderer_style=render_context.json_renderer_style,
                ),
            )
        )
        shown_field_count += 1

    omitted_field_count = len(field_name_to_value) - shown_field_count
    if omitted_field_count > 0:
        lines.append((f"{reference}.<fields>: <snipped {omitted_field_count} public fields>",))

    return lines


def _render_reference_and_value_lines(
    *,
    reference: str,
    value: object,
    render_context: _PromptRenderContext,
    callable_signature_text_to_reference_list: dict[str, list[str]],
) -> list[tuple[UserContent, ...]]:
    if isinstance(value, TypeAliasType):
        return [(f"{reference}: type = {value.__value__}",)]

    if callable(value):
        return [
            (
                _render_callable_line(
                    reference=reference,
                    value=value,
                    callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
                ),
            )
        ]

    prompt_user_content = try_project_user_prompt_value(value)
    if prompt_user_content is not None:
        return [(f"{reference}: {type(value).__name__} = ", *prompt_user_content)]

    if _is_object_capability_candidate(value):
        return _render_object_capability_lines(
            reference=reference,
            value=value,
            render_context=render_context,
            callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
        )

    return [
        (
            _render_value_preview_line(
                reference=reference,
                value=value,
                value_max_tokens=render_context.context_limits.value_max_tokens,
                token_encoding=render_context.token_encoding,
                json_renderer_style=render_context.json_renderer_style,
            ),
        )
    ]


def _render_reference_and_value_list_section(
    *,
    section_name: str,
    step_context: StepContext,
    reference_and_value_list: list[ReferenceAndValue],
    max_items: int,
    section_max_tokens: int,
    render_context: _PromptRenderContext,
) -> PromptContentTuple:
    content_part_list: list[UserContent] = []
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
            render_context=render_context,
            callable_signature_text_to_reference_list=callable_signature_text_to_reference_list,
        )

        rendered_tokens = sum(
            _count_prompt_line_tokens(
                prompt_line=rendered_line,
                token_encoding=render_context.token_encoding,
            )
            for rendered_line in rendered_line_list
        )

        if total_tokens + rendered_tokens > section_max_tokens:
            token_limit_reached = True
            break

        for rendered_line in rendered_line_list:
            _append_prompt_line(content_part_list=content_part_list, prompt_line=rendered_line)
        total_tokens += rendered_tokens
        shown_items += 1

    truncated = shown_items < len(reference_and_value_list)
    if truncated:
        _append_prompt_line(content_part_list=content_part_list, prompt_line=("<snipped>",))
        if token_limit_reached:
            dropped_multimodal = any(try_project_user_prompt_value(value) is not None for _, value in reference_and_value_list[shown_items:])
            log_attributes: dict[str, Any] = {
                STEP_ID: step_context.step_id,
                "nighthawk.prompt_context.section": section_name,
                "nighthawk.prompt_context.reason": "token_limit",
                "nighthawk.prompt_context.rendered_items": shown_items,
                "nighthawk.prompt_context.total_items": len(reference_and_value_list),
                "nighthawk.prompt_context.max_tokens": section_max_tokens,
                "nighthawk.prompt_context.dropped_multimodal": dropped_multimodal,
            }
            # Spec §8.2.2: token truncation can drop explicit dotted multimodal
            # leaves because section ordering and budgets remain authoritative.
            try:
                execution_ref = get_execution_ref()
                log_attributes[RUN_ID] = execution_ref.run_id
                log_attributes[SCOPE_ID] = execution_ref.scope_id
            except Exception:
                pass
            logging.getLogger("nighthawk").info("prompt_context_truncated %s", log_attributes)

    return coalesce_user_content(content_part_list)


def _resolve_reference_path_in_step_context(
    *,
    step_context: StepContext,
    reference_path: str,
) -> object:
    reference_segment_list = reference_path.split(".")
    if not reference_segment_list:
        return _MISSING

    current_value = resolve_name_in_step_context(step_context, reference_segment_list[0])
    if current_value is _MISSING:
        return _MISSING

    for reference_segment in reference_segment_list[1:]:
        field_name_to_value = _resolve_public_field_name_to_value(value=current_value)
        if reference_segment not in field_name_to_value:
            return _MISSING
        current_value = field_name_to_value[reference_segment]

    return current_value


def _collect_explicit_multimodal_reference_and_value_list(
    *,
    step_context: StepContext,
    reference_paths: Iterable[str],
    include_locals: bool,
) -> list[ReferenceAndValue]:
    explicit_reference_and_value_list: list[ReferenceAndValue] = []

    for reference_path in sorted(set(reference_paths)):
        if "." not in reference_path:
            continue

        root_reference = reference_path.split(".", 1)[0]
        if root_reference.startswith("__"):
            continue
        if (root_reference in step_context.step_locals) != include_locals:
            continue

        resolved_value = _resolve_reference_path_in_step_context(
            step_context=step_context,
            reference_path=reference_path,
        )
        if resolved_value is _MISSING:
            continue
        if try_project_user_prompt_value(resolved_value) is None:
            continue

        explicit_reference_and_value_list.append((reference_path, resolved_value))

    return explicit_reference_and_value_list


def _render_locals_section(
    *,
    step_context: StepContext,
    references: Iterable[str],
    explicit_reference_paths: Iterable[str],
    render_context: _PromptRenderContext,
) -> PromptContentTuple:
    eligible_reference_and_value_list: list[ReferenceAndValue] = []
    for reference in sorted(references):
        if reference.startswith("__"):
            continue
        eligible_reference_and_value_list.append((reference, step_context.step_locals[reference]))

    eligible_reference_and_value_list.extend(
        _collect_explicit_multimodal_reference_and_value_list(
            step_context=step_context,
            reference_paths=explicit_reference_paths,
            include_locals=True,
        )
    )
    eligible_reference_and_value_list.sort(key=lambda item: item[0])

    return _render_reference_and_value_list_section(
        section_name="locals",
        step_context=step_context,
        reference_and_value_list=eligible_reference_and_value_list,
        max_items=render_context.context_limits.locals_max_items,
        section_max_tokens=render_context.context_limits.locals_max_tokens,
        render_context=render_context,
    )


def extract_reference_data_and_program(text: str) -> ExtractedReferenceData:
    reference_path_pattern = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"

    unescaped_token_pattern = re.compile(r"(?<!\\)<(" + reference_path_pattern + r")>")
    escaped_token_pattern = re.compile(r"\\<(" + reference_path_pattern + r")>")

    reference_paths: set[str] = set()
    references: set[str] = set()
    for match in unescaped_token_pattern.finditer(text):
        reference_path = match.group(1)
        reference_paths.add(reference_path)
        references.add(reference_path.split(".", 1)[0])

    def unescape(match: re.Match[str]) -> str:
        return f"<{match.group(1)}>"

    unescaped_text = escaped_token_pattern.sub(unescape, text)
    return ExtractedReferenceData(tuple(references), tuple(reference_paths), unescaped_text)


def extract_references_and_program(text: str) -> tuple[tuple[str, ...], str]:
    references, _, program_text = extract_reference_data_and_program(text)
    return references, program_text


def _render_globals_section(
    *,
    step_context: StepContext,
    references: Iterable[str],
    explicit_reference_paths: Iterable[str],
    render_context: _PromptRenderContext,
) -> PromptContentTuple:
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

    eligible_reference_and_value_list.extend(
        _collect_explicit_multimodal_reference_and_value_list(
            step_context=step_context,
            reference_paths=explicit_reference_paths,
            include_locals=False,
        )
    )
    eligible_reference_and_value_list.sort(key=lambda item: item[0])

    return _render_reference_and_value_list_section(
        section_name="globals",
        step_context=step_context,
        reference_and_value_list=eligible_reference_and_value_list,
        max_items=render_context.context_limits.globals_max_items,
        section_max_tokens=render_context.context_limits.globals_max_tokens,
        render_context=render_context,
    )


def _resolve_step_prompt_template_text(
    *,
    template_text: str,
    name_to_value: dict[str, str],
) -> str:
    template = Template(template_text)
    return template.safe_substitute(name_to_value)


def _resolve_step_user_prompt_content(
    *,
    template_text: str,
    name_to_content: Mapping[str, PromptContentTuple],
) -> PromptContentTuple:
    # Manually implements Template.safe_substitute semantics to support multimodal
    # content tuples that the standard string-based safe_substitute cannot handle.
    # Unresolved placeholders and escape sequences are preserved as literal text.
    template = Template(template_text)
    content_part_list: list[UserContent] = []
    cursor = 0

    for match in template.pattern.finditer(template.template):
        start_index, end_index = match.span()
        if start_index > cursor:
            content_part_list.append(template.template[cursor:start_index])

        escaped_name = match.group("escaped")
        named_name = match.group("named") or match.group("braced")
        invalid_expression = match.group("invalid")

        if escaped_name is not None:
            content_part_list.append(template.delimiter)
        elif named_name is not None:
            content_part_list.extend(name_to_content.get(named_name, (match.group(0),)))
        elif invalid_expression is not None:
            content_part_list.append(match.group(0))

        cursor = end_index

    if cursor < len(template.template):
        content_part_list.append(template.template[cursor:])

    return coalesce_user_content(content_part_list)


def _resolve_step_system_prompt_text(*, configuration: StepExecutorConfiguration) -> str:
    return _resolve_step_prompt_template_text(
        template_text=configuration.prompts.step_system_prompt_template,
        name_to_value={
            "tool_result_max_tokens": str(configuration.context_limits.tool_result_max_tokens),
        },
    )


def _resolve_step_user_prompt_content_with_context_limits(
    *,
    template_text: str,
    program_content: PromptContentTuple,
    locals_content: PromptContentTuple,
    globals_content: PromptContentTuple,
    tool_result_max_tokens: int,
) -> PromptContentTuple:
    return _resolve_step_user_prompt_content(
        template_text=template_text,
        name_to_content={
            "program": program_content,
            "locals": locals_content,
            "globals": globals_content,
            "tool_result_max_tokens": (str(tool_result_max_tokens),),
        },
    )


def build_system_prompt(*, configuration: StepExecutorConfiguration) -> str:
    return _resolve_step_system_prompt_text(configuration=configuration)


def build_user_prompt(
    *,
    processed_natural_program: str,
    step_context: StepContext,
    configuration: StepExecutorConfiguration,
) -> PromptContentTuple:
    template_text = configuration.prompts.step_user_prompt_template
    context_limits = configuration.context_limits
    token_encoding = configuration.resolve_token_encoding()

    references, explicit_reference_paths, program_text = extract_reference_data_and_program(processed_natural_program)
    explicit_dotted_reference_path_set = frozenset(reference_path for reference_path in explicit_reference_paths if "." in reference_path)

    render_context = _PromptRenderContext(
        context_limits=context_limits,
        token_encoding=token_encoding,
        json_renderer_style=configuration.json_renderer_style,
        explicit_dotted_reference_path_set=explicit_dotted_reference_path_set,
    )

    locals_content = _render_locals_section(
        step_context=step_context,
        references=step_context.step_locals.keys(),
        explicit_reference_paths=explicit_reference_paths,
        render_context=render_context,
    )

    augmented_global_references = set(references) | set(step_context.implicit_reference_name_to_value.keys())
    globals_content = _render_globals_section(
        step_context=step_context,
        references=augmented_global_references,
        explicit_reference_paths=explicit_reference_paths,
        render_context=render_context,
    )

    prompt_content = _resolve_step_user_prompt_content_with_context_limits(
        template_text=template_text,
        program_content=(program_text,),
        locals_content=locals_content,
        globals_content=globals_content,
        tool_result_max_tokens=context_limits.tool_result_max_tokens,
    )

    suffix_fragments = (
        *configuration.user_prompt_suffix_fragments,
        *_current_user_prompt_suffix_fragments(),
    )
    if suffix_fragments:
        content_part_list: list[UserContent] = list(prompt_content)
        for suffix_fragment in suffix_fragments:
            if content_part_list:
                content_part_list.append("\n\n")
            content_part_list.append(suffix_fragment)
        return coalesce_user_content(content_part_list)

    return prompt_content
