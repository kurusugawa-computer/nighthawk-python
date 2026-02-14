from __future__ import annotations

import json

import tiktoken

from nighthawk.json_renderer import render_json_text

tiktoken_encoding = tiktoken.get_encoding("o200k_base")


def test_strict_style_outputs_strict_json() -> None:
    text, _ = render_json_text(
        {"b": 2, "a": 1},
        max_tokens=10_000,
        encoding=tiktoken_encoding,
        style="strict",
    )

    payload = json.loads(text)
    assert payload == {"a": 1, "b": 2}


def test_cycle_emits_cycle_sentinel() -> None:
    a: list[object] = []
    a.append(a)

    text, _ = render_json_text(
        a,
        max_tokens=10_000,
        encoding=tiktoken_encoding,
        style="strict",
    )

    payload = json.loads(text)
    assert payload == ["<cycle>"]


def test_nonserializable_emits_nonserializable_sentinel() -> None:
    text, _ = render_json_text(
        {"data": b"abc"},
        max_tokens=10_000,
        encoding=tiktoken_encoding,
        style="strict",
    )

    payload = json.loads(text)
    assert payload["data"] == "<nonserializable>"


def test_preserves_order_for_ordered_containers() -> None:
    text, _ = render_json_text(
        [3, 2, 1],
        max_tokens=10_000,
        encoding=tiktoken_encoding,
        style="strict",
    )

    payload = json.loads(text)
    assert payload == [3, 2, 1]


def test_sorts_mapping_keys_by_compact_json_rendering() -> None:
    text, _ = render_json_text(
        {'"': 1, "Z": 2},
        max_tokens=10_000,
        encoding=tiktoken_encoding,
        style="strict",
    )

    payload = json.loads(text)
    assert list(payload.keys()) == ["Z", '"']


def test_returns_minimum_output_when_budget_is_too_small() -> None:
    text, _ = render_json_text(
        {"a": 1},
        max_tokens=2,
        encoding=tiktoken_encoding,
        style="strict",
    )

    assert text == "{}"


def test_output_tokens_are_within_budget_when_possible() -> None:
    value = {"items": list(range(500))}

    budget = 200
    text, _ = render_json_text(
        value,
        max_tokens=budget,
        encoding=tiktoken_encoding,
        style="strict",
    )

    assert len(tiktoken_encoding.encode(text)) <= budget
