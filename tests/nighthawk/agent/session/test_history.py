"""Finite history excerpts, stable horizons, and unrestricted retained-range search."""

from dataclasses import FrozenInstanceError

import pytest

from nighthawk.agent.session import HistoryFilter
from nighthawk.agent.session._history import SessionHistory
from nighthawk.agent.session.errors import SessionClosedError


def test_search_beyond_two_hundred_records_and_stable_continuation() -> None:
    history = SessionHistory()
    for i in range(250):
        history._append(cell_id=str(i), kind="input", text=f"record {i}")
    history._append(cell_id="250", kind="answer", text="needle first")
    history._append(cell_id="251", kind="answer", text="unrelated")
    history._append(cell_id="252", kind="answer", text="needle second")
    first = history.search("needle", maximum_item_count=1)
    assert first.record_tuple[0].record_index == 250
    assert first.next_starting_index == 251
    history._append(cell_id="253", kind="answer", text="needle later")
    second = history.search("needle", starting_index=251, horizon=first.horizon)
    assert [record.record_index for record in second.record_tuple] == [252]
    assert second.next_starting_index is None
    assert history.search("NEEDLE").record_tuple == ()
    assert history.search("missing").next_starting_index is None


def test_limits_bound_stored_text_and_returned_excerpts_independently() -> None:
    history = SessionHistory()
    original = history._append(cell_id="first", kind="input", text="x" * 7000)
    assert original.incomplete and len(original.text) == 6000
    history._append(cell_id="second", kind="answer", text="second")
    page = history.read(maximum_character_count=4)
    assert page.record_tuple[0].text == "xxxx"
    assert page.record_tuple[0].incomplete
    assert page.next_starting_index == 1
    next_page = history.read(starting_index=1, horizon=page.horizon)
    assert next_page.record_tuple[0].text == "second"
    assert not next_page.incomplete
    assert len(history.read(maximum_character_count=99999).record_tuple[0].text) == 6000
    with pytest.raises(FrozenInstanceError):
        original.text = "changed"  # type: ignore[misc]
    history._dispose()
    assert page.record_tuple[0].text == "xxxx"
    with pytest.raises(SessionClosedError):
        history.read()


def test_filtered_history_and_plain_text_preserve_records_and_semantic_newlines() -> None:
    history = SessionHistory()
    original = history._append(cell_id="first", kind="input", text="def sample():\n    return '日本語'\n")
    history._append(cell_id="first", kind="answer", text="A result", run_id="run-one")
    truncated = history._append(cell_id="second", kind="input", text="x" * 7000)
    assert history.read(filters=HistoryFilter(kind="answer", run_id="run-one")).record_tuple[0].text == "A result"
    text = history.text(original.record_index)
    assert text.text == original.text and text.available and not text.incomplete
    assert "Original full text is unavailable" in history.text(truncated.record_index).description
    assert not history.text(999).available
    assert history._record_list[0] is original


@pytest.mark.parametrize(
    "arguments",
    [
        {"starting_index": -1},
        {"horizon": -1},
        {"horizon": 1},
        {"maximum_item_count": 0},
        {"maximum_character_count": 0},
    ],
)
def test_invalid_history_queries_fail(arguments: dict[str, int]) -> None:
    history = SessionHistory()
    with pytest.raises(ValueError):
        history.read(
            starting_index=arguments.get("starting_index", 0),
            horizon=arguments.get("horizon"),
            maximum_item_count=arguments.get("maximum_item_count", 20),
            maximum_character_count=arguments.get("maximum_character_count", 6000),
        )
    with pytest.raises(ValueError):
        history.search("")


def test_outcome_metadata_is_confirmed_and_cannot_cross_history_horizon() -> None:
    from nighthawk.agent.session import CellInput

    from .test_session import session_manager

    session = session_manager().create_session()
    try:
        execution = session.submit(CellInput("value = 42", mode="python"))
        before = session.history.read()
        assert before.cell_outcome_tuple == ()
        assert execution.wait().status == "success"
        after = session.history.read(maximum_item_count=1)
        assert after.record_tuple == before.record_tuple
        assert after.cell_outcome_tuple == ((execution.cell_id, "success"),)
        assert session.history.read(horizon=before.horizon).cell_outcome_tuple == ()
        assert session.history.read(horizon=before.horizon, filters=HistoryFilter(outcome="success")).record_tuple == ()
        assert before.cell_outcome_tuple == ()
    finally:
        session.close()
