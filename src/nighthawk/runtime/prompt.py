from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from string import Template
from typing import Any, NamedTuple, TypeAliasType, cast

import tiktoken
from pydantic_ai.messages import TextContent, UserContent, is_multi_modal_content

from ..configuration import StepContextLimits, StepExecutorConfiguration
from ..json_renderer import JsonRendererStyle, count_tokens, render_json_text
from ._user_content import (
    coalesce_user_content,
    try_project_user_prompt_value,
)
from .description import annotation_text, class_bases, describe_callable, describe_public_members, exact_type, safe_preview, type_name
from .environment import MissingEnvironmentNameError, UnboundEnvironmentNameError
from .scoping import (
    RUN_ID,
    SCOPE_ID,
    STEP_EXECUTION_ID,
    _current_user_prompt_suffix_fragments,
    get_execution_reference,
)
from .step_context import _MISSING, StepContext, resolve_name_in_step_context

type ReferenceAndValue = tuple[str, object]
type PromptContentTuple = tuple[UserContent, ...]

# Fixed internal budgeting heuristic; not a provider-side estimate.
_MULTIMODAL_BINDING_TOKEN_COST = 64
_UNBOUND = object()


@dataclass(frozen=True)
class _PromptRenderContext:
    """Stable rendering invariants for one Natural block prompt pass."""

    context_limits: StepContextLimits
    token_encoding: tiktoken.Encoding
    json_renderer_style: JsonRendererStyle
    explicit_dotted_reference_path_set: frozenset[str]


class ExtractedReferenceData(NamedTuple):
    references: tuple[str, ...]
    reference_paths: tuple[str, ...]
    program_text: str


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


def _resolve_callable_usage_intent_hint(documentation_text: str | None) -> str | None:
    if documentation_text:
        first_line = documentation_text.splitlines()[0].strip()
        if first_line:
            hint = _normalize_usage_intent_text(first_line)
            if _is_meaningful_usage_intent_hint(hint):
                return hint
    return None


def _render_callable_line(
    *,
    reference: str,
    value: object,
) -> str:
    description = describe_callable(value)
    usage_intent_hint = _resolve_callable_usage_intent_hint(description.documentation)
    callable_signature_text = description.signature_text
    if callable_signature_text is None:
        rendered_text = f"{reference}: <callable; signature-unavailable>"
        if usage_intent_hint is not None:
            rendered_text += f"  # {usage_intent_hint}"
        return rendered_text

    rendered_text = f"{reference}: {callable_signature_text}"
    metadata_comment_list: list[str] = []
    if usage_intent_hint is not None:
        metadata_comment_list.append(usage_intent_hint)
    if description.asynchronous:
        metadata_comment_list.append("async")

    if metadata_comment_list:
        rendered_text += f"  # {'; '.join(metadata_comment_list)}"
    return rendered_text


def _is_object_capability_candidate(value: object) -> bool:
    if callable(value):
        return False
    if exact_type(value, TypeAliasType):
        return False
    if value is None:
        return False
    if any(type(value) is candidate for candidate in (bool, int, float, str, bytes, bytearray)):
        return False
    return not any(base is candidate for base in class_bases(type(value)) for candidate in (dict, list, tuple, set, frozenset))


def _resolve_public_method_name_to_value(*, value: object) -> dict[str, object]:
    return dict(describe_public_members(value).method_name_to_value)


def _resolve_public_field_name_to_value(*, value: object) -> dict[str, object]:
    return dict(describe_public_members(value).field_name_to_value)


def _render_value_preview_line(
    *,
    reference: str,
    value: object,
    value_max_tokens: int,
    token_encoding: tiktoken.Encoding,
    json_renderer_style: JsonRendererStyle,
) -> str:
    reference_type_name = type_name(value)
    rendered_name_and_type = f"{reference}: {reference_type_name} = "

    rendered_value, _ = render_json_text(
        safe_preview(value),
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
    # Line-level token counting charges one token for the newline separator.
    total_tokens = 1
    for content in prompt_line:
        if isinstance(content, str):
            total_tokens += count_tokens(content, token_encoding)
        elif isinstance(content, TextContent):
            total_tokens += count_tokens(content.content, token_encoding)
        else:
            total_tokens += _MULTIMODAL_BINDING_TOKEN_COST
    return total_tokens


def _render_object_capability_lines(
    *,
    reference: str,
    value: object,
    render_context: _PromptRenderContext,
) -> list[tuple[UserContent, ...]]:
    lines: list[tuple[UserContent, ...]] = [(f"{reference}: object = {type_name(value)}",)]

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
            # Explicit dotted multimodal references (e.g. <holder.photo>) render as their own top-level line.
            # Skip them here but consume one member slot so truncation stays stable regardless of hoisting.
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
) -> list[tuple[UserContent, ...]]:
    if value is _UNBOUND:
        return [(f"{reference}: <unbound Python binding>",)]
    if exact_type(value, TypeAliasType):
        return [(f"{reference}: type = {annotation_text(value)}; alias expansion omitted (requires evaluation)",)]

    if callable(value):
        return [
            (
                _render_callable_line(
                    reference=reference,
                    value=value,
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

    for reference, value in reference_and_value_list:
        if shown_items >= max_items:
            break

        rendered_line_list = _render_reference_and_value_lines(
            reference=reference,
            value=value,
            render_context=render_context,
        )
        if "." not in reference:
            named_description = step_context.environment.describe(reference)
            annotation = named_description["annotation"]
            output_contract = named_description["output_contract"]
            metadata: list[str] = []
            if output_contract is not None:
                metadata.append(f"output: {output_contract}")
            if annotation is not None and annotation != output_contract:
                metadata.append(f"annotation: {annotation}")
            if metadata:
                first_line = rendered_line_list[0]
                rendered_line_list[0] = (f"[{'; '.join(metadata)}] {first_line[0]}", *first_line[1:])

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
        omitted_names = [name for name, _ in reference_and_value_list[shown_items:]]
        _append_prompt_line(
            content_part_list=content_part_list,
            prompt_line=(
                f"Omitted: {', '.join(omitted_names[:8])}{', ...' if len(omitted_names) > 8 else ''}. Retrieve names with {step_context.environment.runtime_reference}.environment.discover().",
            ),
        )
        if token_limit_reached:
            dropped_multimodal = any(try_project_user_prompt_value(value) is not None for _, value in reference_and_value_list[shown_items:])
            log_attributes: dict[str, Any] = {
                STEP_EXECUTION_ID: step_context.execution_reference.step_execution_id or "",
                "nighthawk.prompt_context.section": section_name,
                "nighthawk.prompt_context.reason": "token_limit",
                "nighthawk.prompt_context.rendered_items": shown_items,
                "nighthawk.prompt_context.total_items": len(reference_and_value_list),
                "nighthawk.prompt_context.max_tokens": section_max_tokens,
                "nighthawk.prompt_context.dropped_multimodal": dropped_multimodal,
            }
            # Token truncation can drop explicit dotted multimodal leaves; section ordering and budgets remain authoritative.
            try:
                execution_reference = get_execution_reference()
                log_attributes[RUN_ID] = execution_reference.run_id
                log_attributes[SCOPE_ID] = execution_reference.scope_id
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
        local_names = step_context.step_locals.keys() | step_context.environment.step_name_to_value.keys() | step_context.binding_commit_targets
        if (root_reference in local_names) != include_locals:
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
        try:
            value = step_context.environment.resolve(reference)
        except (MissingEnvironmentNameError, UnboundEnvironmentNameError):
            value = _UNBOUND
        eligible_reference_and_value_list.append((reference, value))

    eligible_reference_and_value_list.extend(
        _collect_explicit_multimodal_reference_and_value_list(
            step_context=step_context,
            reference_paths=explicit_reference_paths,
            include_locals=True,
        )
    )
    explicit_names = (
        step_context.read_binding_names | step_context.binding_commit_targets | {path.split(".", 1)[0] for path in explicit_reference_paths}
    )
    eligible_reference_and_value_list.sort(
        key=lambda item: (
            0 if item[0].split(".", 1)[0] in explicit_names else 1 if item[0].split(".", 1)[0] in step_context.step_locals else 2,
            item[0],
        )
    )

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
    escaped_token_pattern = re.compile(r"\\<(:?" + reference_path_pattern + r")>")

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
        if (
            reference in step_context.step_locals
            or reference in step_context.environment.step_name_to_value
            or reference in step_context.binding_commit_targets
        ):
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
    explicit_names = (
        step_context.read_binding_names | step_context.binding_commit_targets | {path.split(".", 1)[0] for path in explicit_reference_paths}
    )
    eligible_reference_and_value_list.sort(key=lambda item: (item[0].split(".", 1)[0] not in explicit_names, item[0]))

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


def resolve_system_prompt_template_text(
    *,
    template_text: str,
    tool_result_max_tokens: int,
) -> str:
    return _resolve_step_prompt_template_text(
        template_text=template_text,
        name_to_value={
            "tool_result_max_tokens": str(tool_result_max_tokens),
        },
    )


def _resolve_step_system_prompt_text(*, configuration: StepExecutorConfiguration) -> str:
    return resolve_system_prompt_template_text(
        template_text=configuration.prompts.system_prompt_template,
        tool_result_max_tokens=configuration.context_limits.tool_result_max_tokens,
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


def _build_user_prompt_content(
    *,
    processed_natural_program: str,
    step_context: StepContext,
    configuration: StepExecutorConfiguration,
) -> PromptContentTuple:
    template_text = configuration.prompts.user_prompt_template
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
        references=step_context.step_locals.keys() | step_context.environment.step_name_to_value.keys() | step_context.binding_commit_targets,
        explicit_reference_paths=explicit_reference_paths,
        render_context=render_context,
    )

    augmented_global_references = set(references) | (set(step_context.environment.task_name_to_value) | step_context.type_alias_reference_names)
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

    runtime_reference = step_context.environment.runtime_reference
    access_lines = [
        f"For missing details: {runtime_reference}.inspect(value) returns a metadata dictionary (type, members, preview).",
        f"Names, sources, contracts: {runtime_reference}.environment.discover(), {runtime_reference}.environment.describe('name').",
        "The runtime root cannot be rebound. Annotations are unevaluated hints; output contracts are enforced.",
    ]
    if any(is_multi_modal_content(content) for content in prompt_content):
        access_lines.append("Use attached media directly for perceptual judgments.")
    environment = step_context.environment
    relevant_names = set(references) | step_context.binding_commit_targets | set(step_context.step_locals) | set(environment.task_name_to_value)
    if any(len(cast(list[str], environment.describe(name)["source_references"])) > 1 for name in relevant_names):
        access_lines.append(
            f"For source-specific requests: {runtime_reference}.environment.resolve('name', source='task') (also 'python' or 'step')."
        )
    return coalesce_user_content(("\n".join(access_lines) + "\n\n", *prompt_content))


def append_user_prompt_suffixes(prompt_content: PromptContentTuple, *, configuration: StepExecutorConfiguration) -> PromptContentTuple:
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


def build_user_prompt(
    *,
    processed_natural_program: str,
    step_context: StepContext,
    configuration: StepExecutorConfiguration,
) -> PromptContentTuple:
    """Render the default presentation with configured and scoped suffixes."""
    content = _build_user_prompt_content(
        processed_natural_program=processed_natural_program,
        step_context=step_context,
        configuration=configuration,
    )
    return append_user_prompt_suffixes(content, configuration=configuration)
