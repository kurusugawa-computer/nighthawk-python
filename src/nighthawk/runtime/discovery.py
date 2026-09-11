"""Bounded, stateless pagination for stable name and member listings."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

import tiktoken


def map_discovery_containers(
    value: object, transform_leaf: Callable[[object], object], *, transform_key: Callable[[object], object] | None = None
) -> object:
    """Visit exact builtin containers, treating discovery pages as atomic leaves."""
    active_object_id_set: set[int] = set()

    def visit(item: object) -> object:
        if type(item) not in (dict, list, tuple):
            return transform_leaf(item)
        if id(item) in active_object_id_set:
            return "<cycle>"
        active_object_id_set.add(id(item))
        try:
            if isinstance(item, dict):
                return {(transform_key(key) if transform_key is not None else key): visit(value) for key, value in item.items()}
            assert isinstance(item, (list, tuple))
            return [visit(value) for value in item]
        finally:
            active_object_id_set.remove(id(item))

    return visit(value)


def contains_discovery_result(value: object) -> bool:
    active_object_id_set: set[int] = set()

    def contains(item: object) -> bool:
        if type(item) is DiscoveryResult:
            return True
        if type(item) not in (dict, list, tuple) or id(item) in active_object_id_set:
            return False
        active_object_id_set.add(id(item))
        try:
            if isinstance(item, dict):
                return any(contains(value) for value in item.values())
            assert isinstance(item, (list, tuple))
            return any(contains(value) for value in item)
        finally:
            active_object_id_set.remove(id(item))

    return contains(value)


class DiscoveryResult(dict[str, object]):
    """A description whose references and continuation must survive rendering."""

    def __init__(self, description: dict[str, object], *, item_count: int, repage: Callable[[int], dict[str, object]]) -> None:
        super().__init__(description)
        self._item_count = item_count
        self._repage = repage

    def for_budget(self, max_tokens: int, encoding: tiktoken.Encoding) -> dict[str, object]:
        def fits(description: dict[str, object]) -> bool:
            return len(encoding.encode(json.dumps(description, ensure_ascii=False, separators=(",", ":")))) <= max_tokens

        candidate = dict(self)
        if fits(candidate):
            return candidate
        for count in range(self._item_count - 1, 0, -1):
            candidate = dict(self._repage(count))
            if fits(candidate):
                return candidate
        # A smaller page must advance by the number of names actually shown.
        # Never summarize the cursor or a reference as ordinary prose.
        if "names" in candidate:
            entries = candidate["names"]
            assert isinstance(entries, list)
            minimal: dict[str, object] = {
                "references": [entry["reference"] for entry in entries],
                "metadata_omitted": True,
            }
        else:
            entries = candidate.get("members", [])
            assert isinstance(entries, list)
            minimal = {"members": [{"name": entry["name"], "kind": entry["kind"]} for entry in entries], "metadata_omitted": True}
        if candidate.get("continuation") is not None:
            minimal["continuation"] = candidate["continuation"]
        if "names" not in candidate and candidate.get("next_cursor") is not None:
            minimal["next_cursor"] = candidate["next_cursor"]
        if fits(minimal):
            return minimal
        return {
            "error": "Discovery metadata exceeds the response budget",
            "recovery": "Use Python slices on environment views or increase tool_result_max_tokens.",
        }


@dataclass(frozen=True)
class NamePage:
    names: tuple[str, ...]
    next_cursor: str | None
    error: str | None = None


def page_names(names: tuple[str, ...], *, source: str, cursor: str | None, max_items: int) -> NamePage:
    if type(max_items) is not int or not 1 <= max_items <= 100:
        return NamePage((), None, "max_items must be an integer from 1 to 100")
    fingerprint = hashlib.sha256((source + "\0" + "\0".join(names)).encode()).hexdigest()[:24]
    offset = 0
    if cursor is not None:
        if type(cursor) is not str:
            return NamePage((), None, "The cursor must be a string")
        cursor_fingerprint, separator, offset_text = cursor.partition(":")
        if not separator or cursor_fingerprint != fingerprint or not offset_text.isascii() or not offset_text.isdecimal() or len(offset_text) > 12:
            return NamePage((), None, "The cursor is invalid or the listing changed")
        offset = int(offset_text)
        if offset <= 0 or offset >= len(names):
            return NamePage((), None, "The cursor is outside this listing")
    end = min(offset + max_items, len(names))
    return NamePage(names[offset:end], f"{fingerprint}:{end}" if end < len(names) else None)
