"""Bounded views over caller-retained values, with Unicode character offsets.

Sequence members remain ordinary references. These helpers bound selected content,
not process memory, audit snapshot work, or the rendered size of arbitrary members.
Executor rendering limits remain the final bounds on prompt-facing values.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from pydantic import BeforeValidator

__all__ = [
    "BasicDataValue",
    "SequenceWindow",
    "TextSearchMatch",
    "TextSearchPage",
    "TextWindow",
    "read_text_window",
    "search_text",
    "select_sequence_window",
]


def _require_basic_data(value: object) -> object:
    if not any(type(value) is basic_type for basic_type in (type(None), bool, int, float, str, list, tuple, dict)):
        raise ValueError("retained data must contain actual built-in basic values")
    if type(value) is dict and any(type(key) is not str for key in value):
        raise ValueError("basic-data dictionary keys must be strings")
    return value


type BasicDataValue = Annotated[
    None | bool | int | float | str | list[BasicDataValue] | tuple[BasicDataValue, ...] | dict[str, BasicDataValue],
    BeforeValidator(_require_basic_data),
]


@dataclass(frozen=True, slots=True)
class TextWindow:
    text: str
    starting_character: int
    next_starting_character: int | None
    incomplete: bool


@dataclass(frozen=True, slots=True)
class TextSearchMatch:
    starting_character: int
    ending_character: int
    preview: str


@dataclass(frozen=True, slots=True)
class TextSearchPage:
    match_tuple: tuple[TextSearchMatch, ...]
    next_starting_character: int | None
    scanned_character_count: int
    incomplete: bool


@dataclass(frozen=True, slots=True)
class SequenceWindow[T]:
    item_tuple: tuple[T, ...]
    starting_index: int
    next_starting_index: int | None
    incomplete: bool


def _validate_bounds(start: int, *limits: int) -> None:
    if start < 0 or any(limit <= 0 for limit in limits):
        raise ValueError("Offsets must be nonnegative and limits must be positive")


def read_text_window(
    value: str,
    *,
    starting_character: int = 0,
    maximum_character_count: int = 2000,
    maximum_line_count: int = 100,
) -> TextWindow:
    """Select at most the requested characters and newline-delimited lines.

    A final unterminated line counts as one line; long lines can be split.
    Incompleteness means unread content remains after the selected region.
    """
    _validate_bounds(starting_character, maximum_character_count, maximum_line_count)
    ending_character = min(len(value), starting_character + maximum_character_count)
    position = starting_character
    for _ in range(maximum_line_count):
        newline = value.find("\n", position, ending_character)
        if newline < 0:
            break
        position = newline + 1
    else:
        ending_character = position
    incomplete = ending_character < len(value)
    return TextWindow(value[starting_character:ending_character], starting_character, ending_character if incomplete else None, incomplete)


def search_text(
    value: str,
    query: str,
    *,
    starting_character: int = 0,
    maximum_scanned_character_count: int = 10000,
    maximum_match_count: int = 20,
) -> TextSearchPage:
    """Search literal, case-sensitive candidate starts, including overlapping hits.

    Lookahead counts against the scan limit. Continue at the first unexamined
    candidate start, including on empty pages. Hit previews contain at most 200
    characters and never extend beyond this page's scanned interval.
    """
    _validate_bounds(starting_character, maximum_scanned_character_count, maximum_match_count)
    if not query or maximum_scanned_character_count < len(query):
        raise ValueError("Search requires a nonempty query fitting within the scan limit")
    ending_character = min(len(value), starting_character + maximum_scanned_character_count)
    final_candidate = len(value) - len(query)
    if starting_character > final_candidate:
        return TextSearchPage((), None, 0, False)
    next_candidate = starting_character
    match_list: list[TextSearchMatch] = []
    while len(match_list) < maximum_match_count:
        position = value.find(query, next_candidate, ending_character)
        if position < 0:
            next_candidate = ending_character - len(query) + 1
            break
        match_list.append(TextSearchMatch(position, position + len(query), value[position : min(position + 200, ending_character)]))
        next_candidate = position + 1
    incomplete = next_candidate <= final_candidate
    return TextSearchPage(tuple(match_list), next_candidate if incomplete else None, ending_character - starting_character, incomplete)


def select_sequence_window[T](value: Sequence[T], *, starting_index: int = 0, maximum_item_count: int = 20) -> SequenceWindow[T]:
    """Select members by reference; item count does not bound their rendered size."""
    _validate_bounds(starting_index, maximum_item_count)
    ending_index = min(len(value), starting_index + maximum_item_count)
    incomplete = ending_index < len(value)
    return SequenceWindow(tuple(value[starting_index:ending_index]), starting_index, ending_index if incomplete else None, incomplete)
