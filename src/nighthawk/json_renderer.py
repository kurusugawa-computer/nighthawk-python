from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from typing import Literal

import headson
import tiktoken
from pydantic import BaseModel

from .runtime.discovery import DiscoveryResult, contains_discovery_result, map_discovery_containers

type JsonRendererStyle = Literal["strict", "default", "detailed"]

type JsonableValue = dict[str, "JsonableValue"] | list["JsonableValue"] | str | int | float | bool | None

_SENTINEL_CYCLE = "<cycle>"
_SENTINEL_NONSERIALIZABLE = "<nonserializable>"
_SENTINEL_FUNCTION = "<function>"
_SENTINEL_EXCEPTION = "<exception>"

_MINIMUM_OUTPUT = "{}"
# Fixed lower bound for budget arithmetic; the real count varies by encoding and is recomputed when needed.
_MINIMUM_OUTPUT_ESTIMATED_TOKEN_COUNT = 1


@dataclasses.dataclass(frozen=True)
class DiscoveryProjection:
    value: object
    text: str
    token_count: int


@dataclasses.dataclass(frozen=True)
class _DiscoverySlot:
    index: int


def project_discovery_value(value: object, *, max_tokens: int, encoding: tiktoken.Encoding) -> DiscoveryProjection | None:
    """Budget composed discovery atomically while preserving native content leaves.

    Ordinary leaves are converted once before trying smaller pages, so budget
    selection never repeats application serializers.
    """
    if not contains_discovery_result(value):
        return None
    pages: list[DiscoveryResult] = []

    def prepare_leaf(item: object) -> object:
        if type(item) is DiscoveryResult:
            pages.append(item)
            return _DiscoverySlot(len(pages) - 1)
        return to_jsonable_value(item)

    prepared = map_discovery_containers(
        value, prepare_leaf, transform_key=lambda key: key if isinstance(key, str) else _render_compact_json(to_jsonable_value(key))
    )

    def candidate(page_budget: int) -> DiscoveryProjection:
        replacements = [page.for_budget(page_budget, encoding) for page in pages]
        projected = map_discovery_containers(prepared, lambda item: replacements[item.index] if type(item) is _DiscoverySlot else item)
        text = _render_compact_json(to_jsonable_value(projected))
        replacement_iterator = iter(replacements)
        native_value = map_discovery_containers(value, lambda item: next(replacement_iterator) if type(item) is DiscoveryResult else item)
        return DiscoveryProjection(native_value, text, count_tokens(text, encoding=encoding))

    full = candidate(max_tokens)
    if full.token_count <= max_tokens:
        return full
    best: DiscoveryProjection | None = None
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
        text = _render_compact_json(to_jsonable_value(recovery))
        token_count = count_tokens(text, encoding=encoding)
        if token_count <= max_tokens or not recovery:
            return DiscoveryProjection(recovery, text, token_count)
    raise AssertionError("The minimum discovery response must be returned")


def render_json_text(
    value: object,
    *,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: JsonRendererStyle,
) -> tuple[str, int]:
    """Render *value* as compact JSON summarized by headson within *max_tokens*, returning the text and its token count.

    The minimum output ``{}`` may exceed the budget.
    """

    if max_tokens < _MINIMUM_OUTPUT_ESTIMATED_TOKEN_COUNT:
        raise ValueError(f"max_tokens must be >= {_MINIMUM_OUTPUT_ESTIMATED_TOKEN_COUNT}")

    discovery = project_discovery_value(value, max_tokens=max_tokens, encoding=encoding)
    if discovery is not None:
        return discovery.text, discovery.token_count
    jsonable = to_jsonable_value(value)
    compact_json_input = _render_compact_json(jsonable)
    compact_json_input_token_count = count_tokens(compact_json_input, encoding=encoding)
    if compact_json_input_token_count <= max_tokens:
        return compact_json_input, compact_json_input_token_count

    summarized, summarized_token_count = _maximize_headson_output_under_max_tokens(
        compact_json_input,
        max_tokens=max_tokens,
        encoding=encoding,
        style=style,
    )

    if summarized is None:
        return _MINIMUM_OUTPUT, _MINIMUM_OUTPUT_ESTIMATED_TOKEN_COUNT

    return summarized, summarized_token_count


def to_jsonable_value(value: object) -> JsonableValue:
    """Convert a Python value to a JsonableValue, replacing non-serializable values with sentinels."""
    active_object_id_set: set[int] = set()
    return _to_jsonable_value_inner(value, active_object_id_set=active_object_id_set)


def _to_jsonable_value_inner(value: object, *, active_object_id_set: set[int]) -> JsonableValue:
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
        if isinstance(value, BaseModel):
            dumped = value.model_dump(mode="python")
            return _to_jsonable_value_inner(dumped, active_object_id_set=active_object_id_set)

        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            as_dict = dataclasses.asdict(value)
            return _to_jsonable_value_inner(as_dict, active_object_id_set=active_object_id_set)

        if isinstance(value, Mapping):
            return _mapping_to_jsonable(value, active_object_id_set=active_object_id_set)

        if isinstance(value, (set, frozenset)):
            return _set_to_jsonable(value, active_object_id_set=active_object_id_set)

        if _is_namedtuple_instance(value):
            return _namedtuple_to_jsonable(value, active_object_id_set=active_object_id_set)

        if isinstance(value, Sequence):
            return _sequence_to_jsonable(value, active_object_id_set=active_object_id_set)

        return _SENTINEL_NONSERIALIZABLE
    except Exception:
        return _SENTINEL_NONSERIALIZABLE
    finally:
        active_object_id_set.remove(object_id)


def _is_namedtuple_instance(value: object) -> bool:
    return isinstance(value, tuple) and hasattr(value, "_fields") and callable(getattr(value, "_asdict", None))


def _namedtuple_to_jsonable(value: object, *, active_object_id_set: set[int]) -> JsonableValue:
    as_dict_method = getattr(value, "_asdict", None)
    if not callable(as_dict_method):
        return _SENTINEL_NONSERIALIZABLE
    as_dict = as_dict_method()
    if not isinstance(as_dict, Mapping):
        return _SENTINEL_NONSERIALIZABLE
    return _mapping_to_jsonable(as_dict, active_object_id_set=active_object_id_set)


def _mapping_to_jsonable(value: Mapping[object, object], *, active_object_id_set: set[int]) -> JsonableValue:
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


def _set_to_jsonable(value: set[object] | frozenset[object], *, active_object_id_set: set[int]) -> JsonableValue:
    converted: list[tuple[str, JsonableValue]] = []
    for item_value in value:
        jsonable = _to_jsonable_value_inner(item_value, active_object_id_set=active_object_id_set)
        converted.append((_render_compact_json(jsonable), jsonable))

    converted.sort(key=lambda pair: pair[0])
    return [jsonable for _, jsonable in converted]


def _sequence_to_jsonable(value: Sequence[object], *, active_object_id_set: set[int]) -> JsonableValue:
    try:
        items = list(value)
    except Exception:
        return _SENTINEL_NONSERIALIZABLE

    return [_to_jsonable_value_inner(item, active_object_id_set=active_object_id_set) for item in items]


def _maximize_headson_output_under_max_tokens(
    compact_json_input: str,
    *,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: JsonRendererStyle,
) -> tuple[str | None, int]:
    best_output: str | None = None
    best_output_token_count = 0

    lower = _MINIMUM_OUTPUT_ESTIMATED_TOKEN_COUNT
    high = len(compact_json_input.encode("utf-8"))
    while lower <= high:
        trial = (lower + high) // 2
        candidate = headson.summarize(
            compact_json_input,
            format="json",
            input_format="json",
            style=style,
            byte_budget=trial,
        )

        if candidate == "":
            lower = trial + 1
            continue

        candidate_token_count = count_tokens(candidate, encoding=encoding)
        if candidate_token_count <= max_tokens:
            best_output = candidate
            best_output_token_count = candidate_token_count
            lower = trial + 1
        else:
            high = trial - 1

    return best_output, best_output_token_count


def _render_compact_json(value: JsonableValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def count_tokens(text: str, encoding: tiktoken.Encoding) -> int:
    return len(encoding.encode(text))
