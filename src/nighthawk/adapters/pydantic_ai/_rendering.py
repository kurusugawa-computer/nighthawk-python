from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence

import tiktoken
from pydantic import JsonValue

from nighthawk.core.description import ReferenceScope, ValueText, declared_field_name_to_value, render_value_text
from nighthawk.core.discovery import DiscoveryResult, contains_discovery_result, map_discovery_containers

_SENTINEL_CYCLE = "<cycle>"
_SENTINEL_NONSERIALIZABLE = "<nonserializable>"
_SENTINEL_FUNCTION = "<function>"
_SENTINEL_EXCEPTION = "<exception>"

# Rendering stops before tokenizing text that cannot fit; ordinary text has far fewer characters per token.
_MAX_CHARACTERS_PER_TOKEN = 16
_COMPLETE_LEVEL = 1 << 30
# Room kept for the continuation marker of truncated top-level text.
_TEXT_MARKER_TOKEN_RESERVE = 32

type ShownFields = Callable[[object], Mapping[str, object] | None]


@dataclasses.dataclass(frozen=True)
class _DiscoveryProjection:
    value: object
    text: str
    token_count: int


@dataclasses.dataclass(frozen=True)
class _DiscoverySlot:
    index: int


def render_value_within_budget(
    value: object,
    *,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    max_depth: int,
    path: str | None = None,
    scope: ReferenceScope | None = None,
    shown_fields: ShownFields | None = None,
) -> tuple[ValueText, int]:
    """Render the most detailed Python form of *value* within *max_tokens*, returning it with its token count.

    Shared objects first shown by this rendering are added to *scope*. The minimum rendering may exceed the budget.
    """
    reference_scope = scope if scope is not None else ReferenceScope()
    checkpoint = reference_scope.checkpoint()

    def fits(level: int) -> ValueText | None:
        rendered = render_value_text(
            value,
            level=level,
            max_depth=max_depth,
            path=path,
            scope=reference_scope,
            shown_fields=shown_fields,
            max_characters=max_tokens * _MAX_CHARACTERS_PER_TOKEN,
        )
        reference_scope.rollback(checkpoint)
        if rendered is None or count_tokens(rendered.text, encoding) > max_tokens:
            return None
        return rendered

    best_level = 0
    failed_level: int | None = None
    level = 1
    while True:
        rendered = fits(level)
        if rendered is None:
            failed_level = level
            break
        best_level = level
        if not rendered.truncated:
            break
        level *= 2
    if failed_level is not None:
        lower, upper = best_level + 1, failed_level - 1
        while lower <= upper:
            middle = (lower + upper) // 2
            rendered = fits(middle)
            if rendered is None:
                upper = middle - 1
                continue
            best_level = middle
            if not rendered.truncated:
                break
            lower = middle + 1
    final = render_value_text(value, level=best_level, max_depth=max_depth, path=path, scope=reference_scope, shown_fields=shown_fields)
    assert final is not None
    return final, count_tokens(final.text, encoding)


def render_complete_value_text(value: object, *, max_depth: int) -> str:
    rendered = render_value_text(value, level=_COMPLETE_LEVEL, max_depth=max_depth)
    assert rendered is not None
    return rendered.text


def render_tool_value_text(value: object, *, max_tokens: int, encoding: tiktoken.Encoding, max_depth: int, path: str | None = None) -> str:
    """Render a tool return within *max_tokens*: top-level text stays raw, other values use Python form."""
    if type(value) is not str:
        return render_value_within_budget(value, max_tokens=max_tokens, encoding=encoding, max_depth=max_depth, path=path)[0].text
    token_list = encoding.encode(value[: max_tokens * _MAX_CHARACTERS_PER_TOKEN + 1])
    if len(token_list) <= max_tokens and len(value) <= max_tokens * _MAX_CHARACTERS_PER_TOKEN:
        return value
    shown = encoding.decode(token_list[: max(max_tokens - _TEXT_MARKER_TOKEN_RESERVE, 0)])
    while shown and not value.startswith(shown):
        shown = shown[:-1]
    omitted_count = len(value) - len(shown)
    continuation = "" if path is None else f"; {path}[{len(shown)}:]"
    return f"{shown}\n<+{omitted_count} characters{continuation}>"


def project_discovery_value(value: object, *, max_tokens: int, encoding: tiktoken.Encoding, max_depth: int) -> _DiscoveryProjection | None:
    """Budget composed discovery atomically, measuring the Python form that the model receives."""
    if not contains_discovery_result(value):
        return None
    pages: list[DiscoveryResult] = []

    def prepare_leaf(item: object) -> object:
        if type(item) is DiscoveryResult:
            pages.append(item)
            return _DiscoverySlot(len(pages) - 1)
        return item

    prepared = map_discovery_containers(value, prepare_leaf)

    def text_token_count(item: object) -> tuple[str, int]:
        text = render_complete_value_text(item, max_depth=max_depth)
        return text, count_tokens(text, encoding)

    def candidate(page_budget: int) -> _DiscoveryProjection:
        replacements = [page.for_budget(page_budget, lambda description: text_token_count(description)[1]) for page in pages]
        projected = map_discovery_containers(prepared, lambda item: replacements[item.index] if type(item) is _DiscoverySlot else item)
        text, token_count = text_token_count(projected)
        return _DiscoveryProjection(projected, text, token_count)

    full = candidate(max_tokens)
    if full.token_count <= max_tokens:
        return full
    best: _DiscoveryProjection | None = None
    lower, upper = 1, max_tokens - 1
    while lower <= upper:
        trial = (lower + upper) // 2
        projected = candidate(trial)
        if projected.token_count <= max_tokens:
            best = projected
            lower = trial + 1
        else:
            upper = trial - 1
    if best is not None:
        return best
    for recovery in (
        {"error": "Discovery response exceeds the response budget", "recovery": "Evaluate discovery separately or increase tool_result_max_tokens."},
        {"recovery": "Evaluate discovery separately."},
        {},
    ):
        text, token_count = text_token_count(recovery)
        if token_count <= max_tokens or not recovery:
            return _DiscoveryProjection(recovery, text, token_count)
    raise AssertionError("The minimum discovery response must be returned")


def to_jsonable_value(value: object) -> JsonValue:
    """Convert a Python value to JSON data for records that are not model input, replacing non-serializable values with sentinels.

    Instances contribute their declared fields; application serializers are not called.
    """
    active_object_id_set: set[int] = set()
    return _to_jsonable_value_inner(value, active_object_id_set=active_object_id_set)


def _to_jsonable_value_inner(value: object, *, active_object_id_set: set[int]) -> JsonValue:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return value

    if isinstance(value, str):
        return value

    if isinstance(value, (bytes, bytearray)):
        return _SENTINEL_NONSERIALIZABLE

    if isinstance(value, type) and issubclass(value, BaseException):
        return _SENTINEL_EXCEPTION

    if callable(value):
        return _SENTINEL_FUNCTION

    object_id = id(value)
    if object_id in active_object_id_set:
        return _SENTINEL_CYCLE

    active_object_id_set.add(object_id)
    try:
        field_name_to_value = declared_field_name_to_value(value)
        if field_name_to_value is not None:
            return {name: _to_jsonable_value_inner(item, active_object_id_set=active_object_id_set) for name, item in field_name_to_value.items()}

        if isinstance(value, Mapping):
            return _mapping_to_jsonable(value, active_object_id_set=active_object_id_set)

        if isinstance(value, (set, frozenset)):
            return _set_to_jsonable(value, active_object_id_set=active_object_id_set)

        if isinstance(value, Sequence):
            return _sequence_to_jsonable(value, active_object_id_set=active_object_id_set)

        return _SENTINEL_NONSERIALIZABLE
    except Exception:
        return _SENTINEL_NONSERIALIZABLE
    finally:
        active_object_id_set.remove(object_id)


def _mapping_to_jsonable(value: Mapping[object, object], *, active_object_id_set: set[int]) -> JsonValue:
    # Sort by the compact JSON rendering of each key so ordering is stable regardless of special characters.
    keyed_entries: list[tuple[str, str, object]] = []
    for key, item_value in value.items():
        if isinstance(key, str):
            sort_key = _render_compact_json(key)
            display_key = key
        else:
            display_key = _render_compact_json(_to_jsonable_value_inner(key, active_object_id_set=active_object_id_set))
            sort_key = display_key
        keyed_entries.append((sort_key, display_key, item_value))

    keyed_entries.sort(key=lambda entry: entry[0])

    return {
        display_key: _to_jsonable_value_inner(item_value, active_object_id_set=active_object_id_set) for _, display_key, item_value in keyed_entries
    }


def _set_to_jsonable(value: set[object] | frozenset[object], *, active_object_id_set: set[int]) -> JsonValue:
    converted: list[tuple[str, JsonValue]] = []
    for item_value in value:
        jsonable = _to_jsonable_value_inner(item_value, active_object_id_set=active_object_id_set)
        converted.append((_render_compact_json(jsonable), jsonable))

    converted.sort(key=lambda pair: pair[0])
    return [jsonable for _, jsonable in converted]


def _sequence_to_jsonable(value: Sequence[object], *, active_object_id_set: set[int]) -> JsonValue:
    try:
        items = list(value)
    except Exception:
        return _SENTINEL_NONSERIALIZABLE

    return [_to_jsonable_value_inner(item, active_object_id_set=active_object_id_set) for item in items]


def _render_compact_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def count_tokens(text: str, encoding: tiktoken.Encoding) -> int:
    return len(encoding.encode(text))
