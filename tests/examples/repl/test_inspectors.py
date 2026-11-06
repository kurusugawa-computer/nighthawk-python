"""Stable historical selection survives newly received history and output."""

from examples.repl.inspectors import HistoryInspector
from nighthawk.agent.session import ConversationRecord


def test_inspector_preserves_anchor_during_live_output() -> None:
    inspector = HistoryInspector()
    original = ConversationRecord(100, "first", "input", "samples = [1, 2]")
    inspector.select((original,), 0)
    earlier = ConversationRecord(1, "earlier", "input", "old")
    later = ConversationRecord(101, "later", "answer", "new")
    rows = inspector.rows((earlier, original, later))
    assert rows[inspector.position(rows)] is original
    inspector.query = "samples"
    assert inspector.rows(rows) == (original,)


def test_history_search_and_outcome_filters_share_python_semantics() -> None:
    from examples.repl.state import ScreenState
    from nighthawk.agent.session import CellInput, HistoryFilter, create_session

    session = create_session(execution_model="openai-responses:gpt-6-luna")
    try:
        execution = session.submit(CellInput("text = 'UPPERCASE Straße 日本語'", mode="python"))
        execution.wait()
        state = ScreenState()
        # The status record is deliberately outside this page. Only the owner's
        # confirmed metadata can establish the input record's outcome.
        page = session.history.read(maximum_item_count=1)
        state.receive_history(page)
        inspector = HistoryInspector()
        for query in ("UPPERCASE", "uppercase", "Straße", "STRASSE", "日本語"):
            for filters in (HistoryFilter(), HistoryFilter(outcome="success"), HistoryFilter(outcome="failure")):
                inspector.query, inspector.filters = query, filters
                actual = inspector.rows(page.record_tuple, state.cell_id_to_outcome)
                expected = session.history.search(query, filters=filters).record_tuple
                assert actual == expected
        assert state.history_scope().startswith("History: received 1/2 records; case-sensitive")
        inspector.query = ""
        inspector.filters = HistoryFilter(outcome="success")
        assert inspector.rows((ConversationRecord(5, "unconfirmed", "status", "success"),)) == ()
    finally:
        session.close()
