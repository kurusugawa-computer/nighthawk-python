from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from examples.repl.input import Draft, InputHistory, StaticCompleter


def test_history_search_cancel_restores_complete_draft() -> None:
    history = InputHistory()
    history.add(Draft("samples = [1]", mode="python"))
    original = Draft("  日本語\n    " + "paste " * 2000, 4, "natural_language")
    history.open(original)
    assert history.search("samples")[0].mode == "python"
    assert history.cancel() == original
    history.open(original)
    assert history.accept("missing", 0) == original


def test_literal_natural_recall_preserves_leading_marker() -> None:
    history = InputHistory()
    history.add(Draft("> quoted text", mode="natural_language"))
    history.open(Draft())
    assert history.accept("quoted", 0).submission() == ("> quoted text", "natural_language")


def test_completion_uses_only_received_names() -> None:
    completer = StaticCompleter()
    completer.names = ("session.locals.read", "samples")
    completions = tuple(completer.get_completions(Document("session.lo"), CompleteEvent()))
    assert [item.text for item in completions] == ["session.locals.read"]


def test_navigation_preserves_editing_copies_and_original_history() -> None:
    history = InputHistory()
    first = Draft("first\nlast", mode="python")
    second = Draft("second", mode="natural_language")
    history.add(first)
    history.add(second)
    submitted = tuple(history.entries)
    latest = Draft("unfinished", 3, "python")
    recalled = history.move(latest, -1)
    assert recalled is not None and recalled.text == "second"
    edited = Draft("second edited", 6, "natural_language")
    recalled = history.move(edited, -1)
    assert recalled is not None and recalled.text == first.text
    assert history.move(first, -1) is None
    assert history.move(first, 1) == edited
    assert history.move(edited, 1) == latest
    assert history.move(latest, 1) is None
    assert tuple(history.entries) == submitted
    history.add(edited)
    assert tuple(history.entries[:2]) == submitted
    recalled = history.move(Draft(), -1)
    assert recalled is not None and recalled.text == edited.text


def test_search_uses_submitted_text_and_joins_navigation_without_losing_draft() -> None:
    history = InputHistory()
    history.add(Draft("old", mode="python"))
    history.add(Draft("recent"))
    latest = Draft("unsent", 2, "python")
    history.move(latest, -1)
    edited = Draft("recent edited", 4)
    history.open(edited)
    assert not history.search("edited")
    assert history.accept("old", 0).text == "old"
    assert history.move(Draft("old edited"), 1) == edited
    assert history.move(edited, 1) == latest
    assert [entry.text for entry in history.entries] == ["old", "recent"]


def test_visual_rows_include_wrapping_wide_characters_and_end_cursor() -> None:
    from examples.repl.input import visual_cursor_rows

    rows = visual_cursor_rows("日本語a\ne\u0301x", 7, "#> ")
    assert rows[0] == [(0, 3), (1, 5)]
    assert rows[1] == [(2, 0), (3, 2), (4, 3)]
    assert rows[2] == [(5, 0), (6, 1), (7, 1), (8, 2)]
    assert visual_cursor_rows("1234", 7, "#> ")[-1] == [(4, 0)]
