"""Draft ownership, explicit modes, and reversible editor history search."""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.layout.screen import Char

from nighthawk.session import CellMode


@dataclass(frozen=True)
class Draft:
    text: str = ""
    cursor: int = 0
    mode: CellMode = "natural_language"

    def submission(self) -> tuple[str, CellMode]:
        return self.text, self.mode


def visual_cursor_rows(text: str, width: int, prompt: str) -> list[list[tuple[int, int]]]:
    """Map editable positions to wrapped screen rows, including the end cursor.

    TextArea prefixes only the first logical line. Char shares its rendering of
    wide, combining, and control characters with the terminal renderer.
    """
    width = max(1, width)
    rows: list[list[tuple[int, int]]] = [[]]
    column = 0
    for character in prompt:
        size = Char(character).width
        if column + size > width:
            rows.append([])
            column = 0
        column += size
    for position in range(len(text) + 1):
        character = text[position] if position < len(text) else "\n"
        size = Char(" " if character == "\n" else character).width
        if column + size > width:
            rows.append([])
            column = 0
        rows[-1].append((position, column))
        column += size
        if character == "\n" and position < len(text):
            rows.append([])
            column = 0
    return [row for row in rows if row]


class InputHistory:
    def __init__(self) -> None:
        self.entries: list[Draft] = []
        self.original: Draft | None = None
        self._position: int | None = None
        self._position_to_draft: dict[int, Draft] = {}

    def add(self, draft: Draft) -> None:
        text, mode = draft.submission()
        self.entries.append(Draft(text, len(text), mode))
        self.reset_navigation()

    def reset_navigation(self) -> None:
        self._position = None
        self._position_to_draft.clear()

    def move(self, draft: Draft, direction: int) -> Draft | None:
        """Recall an editing copy without changing any submitted history entry."""
        position = len(self.entries) if self._position is None else self._position
        following = position + direction
        if not 0 <= following <= len(self.entries):
            return None
        self._position_to_draft[position] = draft
        self._position = following
        return self._position_to_draft.get(following, self.entries[following] if following < len(self.entries) else Draft())

    def open(self, draft: Draft) -> None:
        self.original = draft
        position = len(self.entries) if self._position is None else self._position
        self._position_to_draft[position] = draft

    def search(self, text: str) -> tuple[Draft, ...]:
        return tuple(entry for entry in reversed(self.entries) if text.casefold() in entry.text.casefold())

    def cancel(self) -> Draft:
        draft = self.original or Draft()
        self.original = None
        return draft

    def accept(self, query: str, position: int) -> Draft:
        matches = self.search(query)
        if not matches:
            return self.cancel()
        result = matches[min(max(position, 0), len(matches) - 1)]
        self.original = None
        # Search recalls the submitted text, then joins the same navigation session.
        self._position = next(i for i, entry in enumerate(self.entries) if entry is result)
        self._position_to_draft[self._position] = result
        return result


class StaticCompleter(Completer):
    """Only previously received names and canonical metadata are consulted."""

    def __init__(self) -> None:
        self.names: tuple[str, ...] = ()

    def get_completions(self, document: Document, complete_event):
        prefix = document.get_word_before_cursor(WORD=True)
        for name in self.names:
            if name.startswith(prefix):
                yield Completion(name, start_position=-len(prefix))
