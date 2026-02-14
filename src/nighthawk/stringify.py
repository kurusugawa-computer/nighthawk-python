from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel

# Public sentinels. These are stable, serialized spellings.
SENTINEL_SNIPPED = "<snipped>"
SENTINEL_OMITTED = "<omitted>"
SENTINEL_CYCLE = "<cycle>"
SENTINEL_NONSERIALIZABLE = "<nonserializable>"

type Purpose = Literal[
    "strict_json",
    "llm_readable_render",
    "log_strict_json",
    "debug_dump_render",
    "debug_unbounded_render",
]


type JsonableValue = dict[str, "JsonableValue"] | list["JsonableValue"] | str | int | float | bool | None


_MINIMUM_VALID_JSON_OUTPUT_LENGTH = len(json.dumps(SENTINEL_SNIPPED, ensure_ascii=False))


class BoundProfile(BaseModel, extra="forbid"):
    """Bounds applied during conversion and output.

    Each bound is either enabled (a concrete non-negative limit) or disabled (None).

    maximum_output_length is a character bound (Unicode code points via Python len).
    """

    maximum_items: int | None = None
    maximum_depth: int | None = None
    maximum_output_length: int | None = None

    def model_post_init(self, __context: Any) -> None:  # pyright: ignore[reportUnknownParameterType]
        del __context
        if self.maximum_items is not None and self.maximum_items < 0:
            raise ValueError("maximum_items must be >= 0")
        if self.maximum_depth is not None and self.maximum_depth < 0:
            raise ValueError("maximum_depth must be >= 0")
        if self.maximum_output_length is not None and self.maximum_output_length < 0:
            raise ValueError("maximum_output_length must be >= 0")
        if self.maximum_output_length is not None and self.maximum_output_length < _MINIMUM_VALID_JSON_OUTPUT_LENGTH:
            raise ValueError(f"maximum_output_length must be >= {_MINIMUM_VALID_JSON_OUTPUT_LENGTH}")


Adapter = Callable[[object], object]


def default_bound_profile_for_purpose(purpose: Purpose) -> BoundProfile:
    match purpose:
        case "strict_json":
            # Proxy transport should pass long payloads by default.
            return BoundProfile(maximum_items=100, maximum_depth=8, maximum_output_length=None)
        case "llm_readable_render":
            return BoundProfile(maximum_items=100, maximum_depth=8, maximum_output_length=2 * 1024)
        case "log_strict_json":
            return BoundProfile(maximum_items=100, maximum_depth=8, maximum_output_length=4 * 1024)
        case "debug_dump_render":
            return BoundProfile(maximum_items=200, maximum_depth=12, maximum_output_length=20 * 1024)
        case "debug_unbounded_render":
            return BoundProfile(maximum_items=None, maximum_depth=None, maximum_output_length=None)


def render_text(
    value: object,
    *,
    purpose: Purpose,
    bound_profile: BoundProfile | None = None,
    adapter_by_type: Mapping[type[Any], Adapter] | None = None,
) -> str:
    """Render a value as deterministic, LLM-oriented pretty text under explicit bounds."""

    effective_bound_profile = bound_profile or default_bound_profile_for_purpose(purpose)

    jsonable = to_jsonable_value(
        value,
        bound_profile=effective_bound_profile,
        adapter_by_type=adapter_by_type,
    )

    rendered = json.dumps(
        jsonable,
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
    )

    return truncate_text(rendered, maximum_output_length=effective_bound_profile.maximum_output_length)


def render_json(
    value: object,
    *,
    purpose: Purpose,
    bound_profile: BoundProfile | None = None,
    adapter_by_type: Mapping[type[Any], Adapter] | None = None,
) -> str:
    """Serialize a value into syntactically valid, compact JSON under explicit bounds."""

    effective_bound_profile = bound_profile or default_bound_profile_for_purpose(purpose)

    jsonable = to_jsonable_value(
        value,
        bound_profile=effective_bound_profile,
        adapter_by_type=adapter_by_type,
    )

    json_text = json.dumps(
        jsonable,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    maximum_output_length = effective_bound_profile.maximum_output_length
    if maximum_output_length is None:
        return json_text

    if len(json_text) <= maximum_output_length:
        return json_text

    # Preserve JSON validity under an output-length bound by degrading to a minimal value.
    return json.dumps(SENTINEL_SNIPPED, ensure_ascii=False)


def to_jsonable_value(
    value: object,
    *,
    bound_profile: BoundProfile,
    adapter_by_type: Mapping[type[Any], Adapter] | None = None,
) -> JsonableValue:
    """Convert an arbitrary value into a JSON-compatible Python value.

    Contract:
    - Never raises.
    - Deterministic and bounded.
    - Cycles are represented with a fixed sentinel.
    - Unknown/non-serializable values become a fixed sentinel.
    """

    active_object_id_set: set[int] = set()
    return _to_jsonable_value_inner(
        value,
        bound_profile=bound_profile,
        adapter_by_type=adapter_by_type,
        depth=0,
        active_object_id_set=active_object_id_set,
    )


def truncate_text(text: str, *, maximum_output_length: int | None) -> str:
    if maximum_output_length is None:
        return text
    if len(text) <= maximum_output_length:
        return text

    prefix_length = maximum_output_length - len(SENTINEL_SNIPPED)
    if prefix_length <= 0:
        return SENTINEL_SNIPPED

    return text[:prefix_length] + SENTINEL_SNIPPED


def _truncate_string(value: str, *, maximum_output_length: int | None) -> str:
    if maximum_output_length is None:
        return value

    if len(value) <= maximum_output_length:
        return value

    prefix_length = maximum_output_length - len(SENTINEL_SNIPPED)
    if prefix_length <= 0:
        return SENTINEL_SNIPPED

    return value[:prefix_length] + SENTINEL_SNIPPED


def _to_jsonable_value_inner(
    value: object,
    *,
    bound_profile: BoundProfile,
    adapter_by_type: Mapping[type[Any], Adapter] | None,
    depth: int,
    active_object_id_set: set[int],
) -> JsonableValue:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            return SENTINEL_NONSERIALIZABLE
        return value

    if isinstance(value, str):
        return _truncate_string(value, maximum_output_length=bound_profile.maximum_output_length)

    if isinstance(value, (bytes, bytearray)):
        return SENTINEL_NONSERIALIZABLE

    object_id = id(value)
    if object_id in active_object_id_set:
        return SENTINEL_CYCLE

    maximum_depth = bound_profile.maximum_depth
    if maximum_depth is not None and depth >= maximum_depth:
        return SENTINEL_OMITTED

    active_object_id_set.add(object_id)
    try:
        adapted = _try_adapt_value(value, adapter_by_type=adapter_by_type)
        if adapted is not None:
            return _to_jsonable_value_inner(
                adapted,
                bound_profile=bound_profile,
                adapter_by_type=adapter_by_type,
                depth=depth + 1,
                active_object_id_set=active_object_id_set,
            )

        if isinstance(value, BaseModel):
            try:
                dumped = value.model_dump(mode="python")
            except Exception:
                return SENTINEL_NONSERIALIZABLE
            return _to_jsonable_value_inner(
                dumped,
                bound_profile=bound_profile,
                adapter_by_type=adapter_by_type,
                depth=depth + 1,
                active_object_id_set=active_object_id_set,
            )

        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return _dataclass_to_jsonable(
                value,
                bound_profile=bound_profile,
                adapter_by_type=adapter_by_type,
                depth=depth,
                active_object_id_set=active_object_id_set,
            )

        if isinstance(value, Mapping):
            return _mapping_to_jsonable(
                value,
                bound_profile=bound_profile,
                adapter_by_type=adapter_by_type,
                depth=depth,
                active_object_id_set=active_object_id_set,
            )

        if isinstance(value, (set, frozenset)):
            return _set_to_jsonable(
                value,
                bound_profile=bound_profile,
                adapter_by_type=adapter_by_type,
                depth=depth,
                active_object_id_set=active_object_id_set,
            )

        if isinstance(value, Sequence):
            return _sequence_to_jsonable(
                value,
                bound_profile=bound_profile,
                adapter_by_type=adapter_by_type,
                depth=depth,
                active_object_id_set=active_object_id_set,
            )

        return SENTINEL_NONSERIALIZABLE
    except Exception:
        return SENTINEL_NONSERIALIZABLE
    finally:
        active_object_id_set.remove(object_id)


def _try_adapt_value(value: object, *, adapter_by_type: Mapping[type[Any], Adapter] | None) -> object | None:
    if adapter_by_type is None:
        return None

    adapter = adapter_by_type.get(type(value))
    if adapter is None:
        return None

    try:
        adapted = adapter(value)
    except Exception:
        return SENTINEL_NONSERIALIZABLE

    if adapted is value:
        return SENTINEL_NONSERIALIZABLE

    return adapted


def _stable_sort_key(value: JsonableValue) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    except Exception:
        return str(value)


def _dataclass_to_jsonable(
    value: Any,
    *,
    bound_profile: BoundProfile,
    adapter_by_type: Mapping[type[Any], Adapter] | None,
    depth: int,
    active_object_id_set: set[int],
) -> JsonableValue:
    maximum_items = bound_profile.maximum_items

    field_name_to_value: dict[str, JsonableValue] = {}
    omitted = False

    try:
        fields = dataclasses.fields(value)
        for i, field in enumerate(fields):
            if maximum_items is not None and i >= maximum_items:
                omitted = True
                break

            try:
                field_value = getattr(value, field.name)
            except Exception:
                field_name_to_value[field.name] = SENTINEL_NONSERIALIZABLE
                continue

            field_name_to_value[field.name] = _to_jsonable_value_inner(
                field_value,
                bound_profile=bound_profile,
                adapter_by_type=adapter_by_type,
                depth=depth + 1,
                active_object_id_set=active_object_id_set,
            )

    except Exception:
        return SENTINEL_NONSERIALIZABLE

    if omitted and SENTINEL_OMITTED not in field_name_to_value:
        field_name_to_value[SENTINEL_OMITTED] = SENTINEL_OMITTED

    return field_name_to_value


def _mapping_to_jsonable(
    value: Mapping[object, object],
    *,
    bound_profile: BoundProfile,
    adapter_by_type: Mapping[type[Any], Adapter] | None,
    depth: int,
    active_object_id_set: set[int],
) -> JsonableValue:
    maximum_items = bound_profile.maximum_items

    items: list[tuple[str, object]] = []
    omitted = False

    try:
        for i, (key, item_value) in enumerate(value.items()):
            if maximum_items is not None and i >= maximum_items:
                omitted = True
                break
            items.append((str(key), item_value))
    except Exception:
        return SENTINEL_NONSERIALIZABLE

    key_to_value: dict[str, JsonableValue] = {}
    for key, item_value in sorted(items, key=lambda item: item[0]):
        key_to_value[key] = _to_jsonable_value_inner(
            item_value,
            bound_profile=bound_profile,
            adapter_by_type=adapter_by_type,
            depth=depth + 1,
            active_object_id_set=active_object_id_set,
        )

    if omitted and SENTINEL_OMITTED not in key_to_value:
        key_to_value[SENTINEL_OMITTED] = SENTINEL_OMITTED

    return key_to_value


def _sequence_to_jsonable(
    value: Sequence[object],
    *,
    bound_profile: BoundProfile,
    adapter_by_type: Mapping[type[Any], Adapter] | None,
    depth: int,
    active_object_id_set: set[int],
) -> JsonableValue:
    maximum_items = bound_profile.maximum_items

    try:
        items = list(value)
    except Exception:
        return SENTINEL_NONSERIALIZABLE

    if maximum_items is None:
        limited_items = items
        omitted = False
    else:
        limited_items = items[:maximum_items]
        omitted = len(items) > maximum_items

    converted = [
        _to_jsonable_value_inner(
            item,
            bound_profile=bound_profile,
            adapter_by_type=adapter_by_type,
            depth=depth + 1,
            active_object_id_set=active_object_id_set,
        )
        for item in limited_items
    ]

    if omitted:
        converted.append(SENTINEL_OMITTED)

    return converted


def _set_to_jsonable(
    value: set[object] | frozenset[object],
    *,
    bound_profile: BoundProfile,
    adapter_by_type: Mapping[type[Any], Adapter] | None,
    depth: int,
    active_object_id_set: set[int],
) -> JsonableValue:
    maximum_items = bound_profile.maximum_items

    try:
        items = list(value)
    except Exception:
        return SENTINEL_NONSERIALIZABLE

    if maximum_items is None:
        limited_items = items
        omitted = False
    else:
        limited_items = items[:maximum_items]
        omitted = len(items) > maximum_items

    converted = [
        _to_jsonable_value_inner(
            item,
            bound_profile=bound_profile,
            adapter_by_type=adapter_by_type,
            depth=depth + 1,
            active_object_id_set=active_object_id_set,
        )
        for item in limited_items
    ]

    converted_sorted = sorted(converted, key=_stable_sort_key)

    if omitted:
        converted_sorted.append(SENTINEL_OMITTED)

    return converted_sorted


__all__ = [
    "Adapter",
    "BoundProfile",
    "JsonableValue",
    "Purpose",
    "SENTINEL_CYCLE",
    "SENTINEL_NONSERIALIZABLE",
    "SENTINEL_OMITTED",
    "SENTINEL_SNIPPED",
    "default_bound_profile_for_purpose",
    "render_text",
    "render_json",
    "to_jsonable_value",
    "truncate_text",
]
