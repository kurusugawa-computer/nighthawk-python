from __future__ import annotations

import json

import pytest

from nighthawk.stringify import BoundProfile, render_json, render_text, to_jsonable_value


def test_to_jsonable_value_never_raises_for_unknown() -> None:
    class NotSerializable:
        pass

    value = to_jsonable_value(NotSerializable(), bound_profile=BoundProfile(maximum_items=10, maximum_depth=5, maximum_output_length=100))
    assert value == "<nonserializable>"


def test_to_jsonable_value_represents_cycles() -> None:
    a: list[object] = []
    a.append(a)

    value = to_jsonable_value(a, bound_profile=BoundProfile(maximum_items=10, maximum_depth=5, maximum_output_length=100))
    assert value == ["<cycle>"]


def test_to_jsonable_value_omits_extra_items() -> None:
    value = to_jsonable_value([1, 2, 3], bound_profile=BoundProfile(maximum_items=2, maximum_depth=5, maximum_output_length=100))
    assert value == [1, 2, "<omitted>"]


def test_strict_json_always_valid_json() -> None:
    text = render_json({"a": 1}, purpose="strict_json")
    payload = json.loads(text)
    assert payload == {"a": 1}


def test_strict_json_preserves_valid_json_under_length_bound() -> None:
    text = render_json(
        {"a": "x" * 1000},
        purpose="strict_json",
        bound_profile=BoundProfile(maximum_items=10, maximum_depth=5, maximum_output_length=20),
    )

    payload = json.loads(text)
    assert payload == "<snipped>"


@pytest.mark.parametrize(
    ("purpose", "expected_mode"),
    [
        ("strict_json", "json"),
        ("log_strict_json", "json"),
        ("llm_readable_render", "render"),
        ("debug_dump_render", "render"),
        ("debug_unbounded_render", "render"),
    ],
)
def test_entrypoint_modes(purpose: str, expected_mode: str) -> None:
    if expected_mode == "json":
        text = render_json({"a": 1}, purpose=purpose)  # type: ignore[arg-type]
        assert json.loads(text) == {"a": 1}
    else:
        text = render_text({"a": 1}, purpose=purpose)  # type: ignore[arg-type]
        assert isinstance(text, str)
        assert '"a"' in text
        assert "1" in text
