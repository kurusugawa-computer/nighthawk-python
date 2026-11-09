from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

from nighthawk.adapters.pydantic_ai._rendering import render_tool_value_text, render_value_within_budget, to_jsonable_value
from nighthawk.core.description import ReferenceScope

tiktoken_encoding = tiktoken.get_encoding("o200k_base")


@dataclass
class _Node:
    label: str
    next: _Node | None = None


def test_complete_values_keep_python_spelling_and_insertion_order() -> None:
    rendered, _ = render_value_within_budget({"b": None, "a": [True, 1.5, (1,)]}, max_tokens=10_000, encoding=tiktoken_encoding, max_depth=8)

    assert rendered.text == "{'b': None, 'a': [True, 1.5, (1,)]}"
    assert rendered.exact and not rendered.truncated


def test_budget_selects_the_most_detailed_rendering_that_fits_and_states_the_continuation() -> None:
    rendered, token_count = render_value_within_budget(
        {"items": list(range(500))}, max_tokens=60, encoding=tiktoken_encoding, max_depth=8, path="data"
    )

    assert token_count <= 60 and rendered.truncated
    match = re.fullmatch(r"\{'items': \[(.*), <\+(\d+) items; data\['items'\]\[(\d+):\]>\]\}", rendered.text)
    assert match is not None
    shown_count = int(match[3])
    assert match[1] == ", ".join(str(number) for number in range(shown_count)) and int(match[2]) == 500 - shown_count


def test_minimum_rendering_names_the_value_when_nothing_else_fits() -> None:
    rendered, _ = render_value_within_budget({"a": 1}, max_tokens=1, encoding=tiktoken_encoding, max_depth=8, path="data")

    assert rendered.text == "<dict; 1 entry; data>"


def test_shared_objects_are_written_as_the_path_that_first_showed_them() -> None:
    first = _Node("first")
    first.next = first
    scope = ReferenceScope()

    shown, _ = render_value_within_budget(first, max_tokens=200, encoding=tiktoken_encoding, max_depth=8, path="o", scope=scope)
    alias, _ = render_value_within_budget([first], max_tokens=200, encoding=tiktoken_encoding, max_depth=8, path="p", scope=scope)

    assert shown.text == "_Node(label='first', next=o)"
    assert alias.text == "[o]"


def test_rejected_budget_trials_do_not_leave_references_in_the_scope() -> None:
    shared = _Node("shared")
    scope = ReferenceScope()

    render_value_within_budget([shared] * 3, max_tokens=12, encoding=tiktoken_encoding, max_depth=8, path="values", scope=scope)

    assert scope.path(shared) in (None, "values[0]")


def test_cycle_without_a_path_is_marked() -> None:
    loop: list[object] = []
    loop.append(loop)

    rendered, _ = render_value_within_budget(loop, max_tokens=100, encoding=tiktoken_encoding, max_depth=8)

    assert rendered.text == "[<cycle list>]"


def test_values_deeper_than_max_depth_stop_at_the_depth_cut() -> None:
    rendered, _ = render_value_within_budget([[[[["deep"]]]]], max_tokens=10_000, encoding=tiktoken_encoding, max_depth=3)

    assert rendered.text == "[[[<list; 1 item>]]]"
    assert not rendered.truncated and not rendered.exact


def test_top_level_text_stays_raw_and_states_its_continuation() -> None:
    text = "line of the request\n" * 400

    assert render_tool_value_text("short\ntext", max_tokens=100, encoding=tiktoken_encoding, max_depth=8) == "short\ntext"
    rendered = render_tool_value_text(text, max_tokens=100, encoding=tiktoken_encoding, max_depth=8, path="request")
    shown, marker = rendered.rsplit("\n", 1)
    assert text.startswith(shown) and len(tiktoken_encoding.encode(rendered)) <= 100
    assert marker == f"<+{len(text) - len(shown)} characters; request[{len(shown)}:]>"


def test_json_records_read_declared_fields_and_mark_cycles() -> None:
    node = _Node("first")
    node.next = node

    assert to_jsonable_value({"node": node, "data": b"abc"}) == {"data": "<nonserializable>", "node": {"label": "first", "next": "<cycle>"}}
