from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from typing import Literal, Tuple

import headson
import tiktoken
from pydantic import BaseModel

type RenderStyle = Literal["strict", "default", "detailed"]

type JsonableValue = dict[str, "JsonableValue"] | list["JsonableValue"] | str | int | float | bool | None

SENTINEL_CYCLE = "<cycle>"
SENTINEL_NONSERIALIZABLE = "<nonserializable>"

_MINIMUM_OUTPUT = "{}"
_MINIMUM_OUTPUT_TOKEN_COUNT = len(_MINIMUM_OUTPUT)  # estimate token count roughly


def render_json_text(
    value: object,
    *,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: RenderStyle,
) -> Tuple[str, int]:
    """Render a JSON-like Python value to JSON-family text under a token budget.

    The value is converted into a JSONable value (JSON-compatible Python types plus sentinel strings for cycles and non-serializable values), then rendered to compact JSON. That compact JSON is summarized with headson under a byte budget chosen to
    maximize output token count while staying within the caller-provided token budget.

    Minimum-output rule: This function may return "{}" even if it exceeds the token budget.

    Args:
        value: The Python value to render.
        max_tokens: The maximum number of tokens allowed in the output.
        encoding: The tiktoken encoding to use for token counting.
        style: The headson rendering style to use.

    Returns:
        A tuple of (rendered text, token count of rendered text).
    """

    if max_tokens < _MINIMUM_OUTPUT_TOKEN_COUNT:
        raise ValueError(f"max_tokens must be >= {_MINIMUM_OUTPUT_TOKEN_COUNT}")

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
        return _MINIMUM_OUTPUT, _MINIMUM_OUTPUT_TOKEN_COUNT

    return summarized, summarized_token_count


def to_jsonable_value(value: object) -> JsonableValue:
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
        return SENTINEL_NONSERIALIZABLE

    object_id = id(value)
    if object_id in active_object_id_set:
        return SENTINEL_CYCLE

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

        if isinstance(value, Sequence):
            return _sequence_to_jsonable(value, active_object_id_set=active_object_id_set)

        return SENTINEL_NONSERIALIZABLE
    except Exception:
        return SENTINEL_NONSERIALIZABLE
    finally:
        active_object_id_set.remove(object_id)


def _mapping_to_jsonable(value: Mapping[object, object], *, active_object_id_set: set[int]) -> JsonableValue:
    items: list[tuple[str, str, object]] = []

    for key, item_value in value.items():
        key_jsonable = _to_jsonable_value_inner(key, active_object_id_set=active_object_id_set)
        key_json_text = _render_compact_json(key_jsonable)
        key_text = key if isinstance(key, str) else key_json_text
        items.append((key_json_text, key_text, item_value))

    items.sort(key=lambda item: (item[0], item[1]))

    key_to_value: dict[str, JsonableValue] = {}
    for _, key_text, item_value in items:
        key_to_value[key_text] = _to_jsonable_value_inner(item_value, active_object_id_set=active_object_id_set)

    return key_to_value


def _set_to_jsonable(value: set[object] | frozenset[object], *, active_object_id_set: set[int]) -> JsonableValue:
    converted: list[JsonableValue] = []
    for item_value in value:
        converted.append(_to_jsonable_value_inner(item_value, active_object_id_set=active_object_id_set))

    return converted


def _sequence_to_jsonable(value: Sequence[object], *, active_object_id_set: set[int]) -> JsonableValue:
    try:
        items = list(value)
    except Exception:
        return SENTINEL_NONSERIALIZABLE

    return [_to_jsonable_value_inner(item, active_object_id_set=active_object_id_set) for item in items]


def _maximize_headson_output_under_max_tokens(
    compact_json_input: str,
    *,
    max_tokens: int,
    encoding: tiktoken.Encoding,
    style: RenderStyle,
) -> Tuple[str | None, int]:
    best_output: str | None = None
    best_output_token_count = 0

    lower = _MINIMUM_OUTPUT_TOKEN_COUNT
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


__all__ = [
    "SENTINEL_CYCLE",
    "SENTINEL_NONSERIALIZABLE",
    "JsonableValue",
    "RenderStyle",
    "count_tokens",
    "render_json_text",
    "to_jsonable_value",
]
