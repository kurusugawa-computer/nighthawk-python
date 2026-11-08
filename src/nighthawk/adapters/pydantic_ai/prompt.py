from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import MISSING, dataclass, fields, is_dataclass
from string import Template
from typing import Annotated, Any, NamedTuple, TypeAliasType, get_args, get_origin

import tiktoken
from pydantic import BaseModel
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    TextContent,
    UploadedFile,
    UserContent,
    VideoUrl,
    is_multi_modal_content,
)

from nighthawk.adapters.pydantic_ai._rendering import count_tokens, render_value_within_budget
from nighthawk.adapters.pydantic_ai._user_content import (
    coalesce_user_content,
    try_project_user_prompt_value,
)
from nighthawk.adapters.pydantic_ai.configuration import StepContextLimits, StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.scoping import get_model_scope
from nighthawk.core.description import (
    ReferenceScope,
    annotation_text,
    class_bases,
    describe_callable,
    describe_public_members,
    exact_type,
    is_reference_target,
    type_name,
)
from nighthawk.core.environment import MissingEnvironmentNameError, UnboundEnvironmentNameError
from nighthawk.core.scoping import (
    RUN_ID,
    SCOPE_ID,
    STEP_EXECUTION_ID,
    optional_scope,
)
from nighthawk.core.step_context import StepContext

type ReferenceAndValue = tuple[str, object]
type PromptContentTuple = tuple[UserContent, ...]

# Fixed internal budgeting heuristic; not a provider-side estimate.
_MULTIMODAL_BINDING_TOKEN_COST = 64
_UNBOUND = object()
_MISSING = object()
_MAX_CONTRACT_TYPE_COUNT = 8


@dataclass(frozen=True)
class _PromptRenderContext:
    """Rendering inputs for one Natural block prompt pass; the reference scope spans the locals and globals sections."""

    context_limits: StepContextLimits
    token_encoding: tiktoken.Encoding
    explicit_dotted_reference_path_set: frozenset[str]
    reference_scope: ReferenceScope


class _ExtractedReferenceData(NamedTuple):
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
    annotation: str | None = None,
) -> str:
    description = describe_callable(value)
    usage_intent_hint = _resolve_callable_usage_intent_hint(description.documentation)
    callable_signature_text = description.signature_text or "signature unavailable"
    if description.asynchronous:
        callable_signature_text = f"async {callable_signature_text}"
    rendered_text = f"{reference}: {annotation or 'Callable'}  # {callable_signature_text}"
    if usage_intent_hint is not None:
        rendered_text += f"; {usage_intent_hint}"
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


def _render_value_preview_line(
    *,
    reference: str,
    value: object,
    value_max_tokens: int,
    render_context: _PromptRenderContext,
    annotation: str | None = None,
) -> str:
    reference_type_name = annotation or ("None" if value is None else type_name(value))
    rendered, _ = render_value_within_budget(
        value,
        max_tokens=value_max_tokens,
        encoding=render_context.token_encoding,
        max_depth=render_context.context_limits.value_max_depth,
        path=reference,
        scope=render_context.reference_scope,
        shown_fields=_media_fields,
    )
    separator = " = " if rendered.exact else "  # preview: "
    return f"{reference}: {reference_type_name}{separator}{rendered.text}"


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
    annotation: str | None = None,
    field_name_to_annotation: Mapping[str, str] | None = None,
) -> list[tuple[UserContent, ...]]:
    value_type_name = type_name(value)
    instance_comment = "instance" if annotation is None or annotation == value_type_name else f"{value_type_name} instance"
    lines: list[tuple[UserContent, ...]] = [(f"{reference}: {annotation or value_type_name}  # {instance_comment}",)]
    if is_reference_target(value):
        render_context.reference_scope.add(value, reference)

    public_members = describe_public_members(value)
    method_name_to_value = public_members.method_name_to_value
    field_name_to_value = public_members.field_name_to_value

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
        lines.append((f"# {reference}: {omitted_method_count} public methods omitted",))

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
                    render_context=render_context,
                    annotation=None if field_name_to_annotation is None else field_name_to_annotation.get(field_name),
                ),
            )
        )
        shown_field_count += 1

    omitted_field_count = len(field_name_to_value) - shown_field_count
    if omitted_field_count > 0:
        lines.append((f"# {reference}: {omitted_field_count} public fields omitted",))

    return lines


def _declared_field_list(contract: object) -> list[tuple[str, object, str]] | None:
    """Return declared fields of a Pydantic model or dataclass contract without evaluating annotations.

    Pydantic and dataclasses resolve field annotations when the class is defined; this reads the stored results.
    """
    if isinstance(contract, type) and issubclass(contract, BaseModel):
        return [
            (
                name,
                information.annotation,
                "required"
                if information.is_required()
                else "default factory"
                if information.default_factory is not None
                else f"default {_default_text(information.default)}",
            )
            for name, information in contract.model_fields.items()
        ]
    if isinstance(contract, type) and is_dataclass(contract):
        return [
            (
                declared_field.name,
                declared_field.type,
                "default factory"
                if declared_field.default_factory is not MISSING
                else "required"
                if declared_field.default is MISSING
                else f"default {_default_text(declared_field.default)}",
            )
            for declared_field in fields(contract)
        ]
    return None


def _default_text(default: object) -> str:
    # Only exact builtin values are spelled, so application representation methods never run.
    if default is None or exact_type(default, bool) or exact_type(default, int) or exact_type(default, float):
        return repr(default)
    if exact_type(default, str):
        return repr(default) if len(default) <= 40 else f"str of length {len(default)}"
    if any(exact_type(default, container_type) for container_type in (tuple, list, dict, set, frozenset)) and not default:  # type: ignore[arg-type]
        return repr(default)
    return f"{type_name(default)} value"


def _collect_contract_types(annotation: object, collected_type_list: list[type]) -> None:
    if len(collected_type_list) >= _MAX_CONTRACT_TYPE_COUNT or annotation in collected_type_list:
        return
    declared_field_list = _declared_field_list(annotation)
    if declared_field_list is not None:
        collected_type_list.append(annotation)  # type: ignore[arg-type]
        for _, field_annotation, _ in declared_field_list:
            _collect_contract_types(field_annotation, collected_type_list)
        return
    if get_origin(annotation) is Annotated:
        _collect_contract_types(get_args(annotation)[0], collected_type_list)
        return
    for argument in get_args(annotation):
        _collect_contract_types(argument, collected_type_list)


def _declared_field_lines(
    *,
    reference: str,
    declared_field_list: list[tuple[str, object, str]],
    max_fields: int,
) -> list[tuple[UserContent, ...]]:
    lines: list[tuple[UserContent, ...]] = [
        (f"{reference}.{name}: {annotation_text(field_annotation)}  # {requirement}",)
        for name, field_annotation, requirement in declared_field_list[:max_fields]
    ]
    if len(declared_field_list) > max_fields:
        lines.append((f"# {reference}: {len(declared_field_list) - max_fields} declared fields omitted",))
    return lines


def _render_contract_lines(
    *,
    reference: str,
    value: object,
    contract: object,
    render_context: _PromptRenderContext,
    described_type_list: list[type],
) -> tuple[list[tuple[UserContent, ...]], Mapping[str, str] | None]:
    """Describe the declared structure of an output contract as Python field declarations.

    Returns lines for an unbound root output and for declared types not yet in ``described_type_list``,
    plus root field annotations that replace runtime types when the current value is an instance of the root type.
    Newly described types are appended to ``described_type_list``.
    """
    collected_type_list: list[type] = []
    _collect_contract_types(contract, collected_type_list)
    if not collected_type_list:
        return [], None
    root_type = get_args(contract)[0] if get_origin(contract) is Annotated else contract
    max_fields = render_context.context_limits.object_max_fields
    lines: list[tuple[UserContent, ...]] = []
    root_field_name_to_annotation: Mapping[str, str] | None = None
    for declared_type in collected_type_list:
        declared_field_list = _declared_field_list(declared_type)
        assert declared_field_list is not None
        if declared_type is root_type and value is _UNBOUND:
            lines.extend(_declared_field_lines(reference=reference, declared_field_list=declared_field_list, max_fields=max_fields))
        elif declared_type is root_type and isinstance(value, declared_type):
            root_field_name_to_annotation = {name: annotation_text(field_annotation) for name, field_annotation, _ in declared_field_list}
        elif declared_type not in described_type_list:
            lines.extend(
                _declared_field_lines(reference=annotation_text(declared_type), declared_field_list=declared_field_list, max_fields=max_fields)
            )
            described_type_list.append(declared_type)
    return lines, root_field_name_to_annotation


def _render_reference_and_value_lines(
    *,
    reference: str,
    value: object,
    render_context: _PromptRenderContext,
    annotation: str | None = None,
    contract: object = _MISSING,
    described_type_list: list[type] | None = None,
) -> list[tuple[UserContent, ...]]:
    contract_lines: list[tuple[UserContent, ...]] = []
    field_name_to_annotation: Mapping[str, str] | None = None
    if contract is not _MISSING:
        contract_lines, field_name_to_annotation = _render_contract_lines(
            reference=reference,
            value=value,
            contract=contract,
            render_context=render_context,
            described_type_list=[] if described_type_list is None else described_type_list,
        )
    if value is _UNBOUND:
        return [(f"{reference}: {annotation or 'object'}  # unbound",), *contract_lines]
    if exact_type(value, TypeAliasType):
        return [(f"{reference}: {annotation or 'TypeAliasType'}  # {annotation_text(value)}; alias expansion omitted (requires evaluation)",)]

    if callable(value):
        return [
            (
                _render_callable_line(
                    reference=reference,
                    value=value,
                    annotation=annotation,
                ),
            )
        ]

    prompt_user_content = try_project_user_prompt_value(value)
    if prompt_user_content is not None:
        return [(f"{reference}: {annotation or type_name(value)}  # attached content: ", *prompt_user_content)]

    if _is_object_capability_candidate(value):
        return [
            *_render_object_capability_lines(
                reference=reference,
                value=value,
                render_context=render_context,
                annotation=annotation,
                field_name_to_annotation=field_name_to_annotation,
            ),
            *contract_lines,
        ]

    return [
        (
            _render_value_preview_line(
                reference=reference,
                value=value,
                value_max_tokens=render_context.context_limits.value_max_tokens,
                render_context=render_context,
                annotation=annotation,
            ),
        ),
        *contract_lines,
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
    described_contract_type_list: list[type] = []

    for reference, value in reference_and_value_list:
        if shown_items >= max_items:
            break

        annotation = None
        contract: object = _MISSING
        if "." not in reference:
            named_description = step_context.environment.describe(reference)
            selected_annotation = named_description["output_contract"] or named_description["annotation"]
            if isinstance(selected_annotation, str):
                annotation = selected_annotation
            if named_description["output_contract"] is not None:
                contract = step_context.environment.binding_name_to_type.get(reference, _MISSING)
        pending_described_type_list = list(described_contract_type_list)
        reference_checkpoint = render_context.reference_scope.checkpoint()
        rendered_line_list = _render_reference_and_value_lines(
            reference=reference,
            value=value,
            render_context=render_context,
            annotation=annotation,
            contract=contract,
            described_type_list=pending_described_type_list,
        )

        rendered_tokens = sum(
            _count_prompt_line_tokens(
                prompt_line=rendered_line,
                token_encoding=render_context.token_encoding,
            )
            for rendered_line in rendered_line_list
        )

        if total_tokens + rendered_tokens > section_max_tokens:
            render_context.reference_scope.rollback(reference_checkpoint)
            token_limit_reached = True
            break

        for rendered_line in rendered_line_list:
            _append_prompt_line(content_part_list=content_part_list, prompt_line=rendered_line)
        total_tokens += rendered_tokens
        shown_items += 1
        described_contract_type_list = pending_described_type_list

    truncated = shown_items < len(reference_and_value_list)
    if truncated:
        _append_prompt_line(content_part_list=content_part_list, prompt_line=("# <snipped>",))
        omitted_names = [name for name, _ in reference_and_value_list[shown_items:]]
        _append_prompt_line(
            content_part_list=content_part_list,
            prompt_line=(
                f"# Omitted: {', '.join(omitted_names[:8])}{', ...' if len(omitted_names) > 8 else ''}. "
                f"Retrieve names with {step_context.environment.runtime_reference}.environment.discover(); "
                f"binding details with {step_context.environment.runtime_reference}.environment.describe('name').",
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
            active_scope = optional_scope()
            if active_scope is not None:
                log_attributes[RUN_ID] = active_scope.execution_reference.run_id
                log_attributes[SCOPE_ID] = active_scope.execution_reference.scope_id
            logging.getLogger("nighthawk").info("prompt_context_truncated %s", log_attributes)

    return coalesce_user_content(content_part_list)


def _resolve_name_in_step_context(step_context: StepContext, name: str) -> object:
    try:
        return step_context.environment.resolve(name)
    except UnboundLocalError:
        raise
    except NameError:
        return _MISSING


def _resolve_reference_path_in_step_context(
    *,
    step_context: StepContext,
    reference_path: str,
) -> object:
    reference_segment_list = reference_path.split(".")
    try:
        current_value = _resolve_name_in_step_context(step_context, reference_segment_list[0])
    except UnboundEnvironmentNameError:
        return _MISSING
    if current_value is _MISSING:
        return _MISSING

    for reference_segment in reference_segment_list[1:]:
        field_name_to_value = describe_public_members(current_value).field_name_to_value
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
        local_names = step_context.step_locals.keys() | step_context.environment.step_name_to_value.keys() | step_context.environment.output_name_set
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
        step_context.read_binding_names | step_context.environment.output_name_set | {path.split(".", 1)[0] for path in explicit_reference_paths}
    )
    eligible_reference_and_value_list.sort(
        key=lambda item: (
            0
            if item[0].split(".", 1)[0] in explicit_names
            else 1
            if item[0].split(".", 1)[0] in step_context.step_locals or item[0].split(".", 1)[0] in step_context.environment.step_name_to_value
            else 2,
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


def extract_reference_data_and_program(text: str) -> _ExtractedReferenceData:
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
    return _ExtractedReferenceData(tuple(references), tuple(reference_paths), unescaped_text)


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
            or reference in step_context.environment.output_name_set
        ):
            continue
        try:
            value = _resolve_name_in_step_context(step_context, reference)
        except UnboundEnvironmentNameError:
            value = _UNBOUND
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
        step_context.read_binding_names | step_context.environment.output_name_set | {path.split(".", 1)[0] for path in explicit_reference_paths}
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
    return Template(template_text).safe_substitute({"tool_result_max_tokens": str(tool_result_max_tokens)})


def build_system_prompt(*, configuration: StepExecutorConfiguration) -> str:
    return resolve_system_prompt_template_text(
        template_text=configuration.system_prompt_template,
        tool_result_max_tokens=configuration.context_limits.tool_result_max_tokens,
    )


def _build_user_prompt_content(
    *,
    processed_natural_program: str,
    step_context: StepContext,
    configuration: StepExecutorConfiguration,
) -> PromptContentTuple:
    template_text = configuration.user_prompt_template
    context_limits = configuration.context_limits
    token_encoding = configuration.resolve_token_encoding()

    references, explicit_reference_paths, program_text = extract_reference_data_and_program(processed_natural_program)
    explicit_dotted_reference_path_set = frozenset(reference_path for reference_path in explicit_reference_paths if "." in reference_path)

    render_context = _PromptRenderContext(
        context_limits=context_limits,
        token_encoding=token_encoding,
        explicit_dotted_reference_path_set=explicit_dotted_reference_path_set,
        reference_scope=ReferenceScope(),
    )

    locals_content = _render_locals_section(
        step_context=step_context,
        references=step_context.step_locals.keys() | step_context.environment.step_name_to_value.keys() | step_context.environment.output_name_set,
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

    prompt_content = _resolve_step_user_prompt_content(
        template_text=template_text,
        name_to_content={
            "program": (program_text,),
            "locals": locals_content,
            "globals": globals_content,
            "tool_result_max_tokens": (str(context_limits.tool_result_max_tokens),),
        },
    )

    if any(is_multi_modal_content(content) for content in prompt_content):
        return coalesce_user_content(("Use attached media directly for perceptual judgments.\n\n", *prompt_content))
    return prompt_content


def append_user_prompt_suffixes(prompt_content: PromptContentTuple, *, configuration: StepExecutorConfiguration) -> PromptContentTuple:
    suffix_fragments = (
        *configuration.user_prompt_suffix_fragments,
        *get_model_scope().user_prompt_suffix_fragments,
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


def _media_fields(value: object) -> dict[str, object] | None:
    if any(type(value) is candidate for candidate in (BinaryContent, ImageUrl, AudioUrl, DocumentUrl, VideoUrl, UploadedFile)):
        namespace = object.__getattribute__(value, "__dict__")
        return {name: item for name, item in namespace.items() if name in ("kind", "media_type", "url", "file_id", "provider_name", "data")}
    return None
