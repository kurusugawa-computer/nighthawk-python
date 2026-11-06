"""Append-only text snapshots and bounded record-index/horizon queries.

Limits bound returned text, not retained storage or search computation.
Queries are ordinary usability contracts, independent of authority enforcement.
"""

from __future__ import annotations

from dataclasses import replace

from nighthawk.session._records import CellStatus, HistoryPage, HistoryRecord
from nighthawk.session._source import require_operator
from nighthawk.session.errors import SessionClosedError
from nighthawk.session.history import matches_history_record
from nighthawk.session.operator_contract import HistoryFilter, PlainTextObservation

_DEFAULT_HISTORY_FILTER = HistoryFilter()


def page_limits(starting_index: int, maximum_item_count: int, maximum_character_count: int) -> tuple[int, int]:
    if starting_index < 0 or maximum_item_count <= 0 or maximum_character_count <= 0:
        raise ValueError("offset must be nonnegative and page limits must be positive")
    return min(maximum_item_count, 20), min(maximum_character_count, 6000)


class SessionHistory:
    """Immutable searchable observations retained until confirmed Session close.

    Each stored record contains at most 6000 text characters; longer input is
    explicitly truncated. A captured exclusive horizon gives stable paging
    while later cells append records. Search uses case-sensitive literal text.
    """

    def __init__(self) -> None:
        self._record_list: list[HistoryRecord] = []
        self._cell_id_to_outcome: dict[str, CellStatus] = {}
        self._cell_id_to_outcome_index: dict[str, int] = {}
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise SessionClosedError("Session history has been disposed")

    @property
    def _count(self) -> int:
        self._require_open()
        return len(self._record_list)

    def _append(
        self,
        *,
        cell_id: str,
        kind: str,
        text: str,
        run_id: str | None = None,
        event_id: str | None = None,
    ) -> HistoryRecord:
        self._require_open()
        record = HistoryRecord(len(self._record_list), cell_id, kind, text[:6000], run_id, event_id, len(text) > 6000)
        self._record_list.append(record)
        return record

    def read(
        self,
        *,
        starting_index: int = 0,
        horizon: int | None = None,
        maximum_item_count: int = 20,
        maximum_character_count: int = 6000,
        filters: HistoryFilter = _DEFAULT_HISTORY_FILTER,
    ) -> HistoryPage:
        """Read a bounded page, preserving original record order and identities."""

        require_operator("history_observation")
        return self._page(None, starting_index, horizon, maximum_item_count, maximum_character_count, filters)

    def search(
        self,
        query: str,
        *,
        starting_index: int = 0,
        horizon: int | None = None,
        maximum_item_count: int = 20,
        maximum_character_count: int = 6000,
        filters: HistoryFilter = _DEFAULT_HISTORY_FILTER,
    ) -> HistoryPage:
        """Search all retained text within the horizon for a nonempty literal."""

        require_operator("history_search")
        if not query:
            raise ValueError("history search requires a nonempty query")
        return self._page(query, starting_index, horizon, maximum_item_count, maximum_character_count, filters)

    def _page(
        self,
        query: str | None,
        starting_index: int,
        horizon: int | None,
        maximum_item_count: int,
        maximum_character_count: int,
        filters: HistoryFilter = _DEFAULT_HISTORY_FILTER,
    ) -> HistoryPage:
        self._require_open()
        item_limit, remaining = page_limits(starting_index, maximum_item_count, maximum_character_count)
        captured_horizon = len(self._record_list) if horizon is None else horizon
        if captured_horizon < 0 or captured_horizon > len(self._record_list):
            raise ValueError("history horizon must be within the retained record count")
        selected: list[HistoryRecord] = []
        cell_id_to_outcome: dict[str, CellStatus] = {}
        continuation: int | None = None
        for i in range(starting_index, captured_horizon):
            record = self._record_list[i]
            outcome = (
                self._cell_id_to_outcome.get(record.cell_id)
                if self._cell_id_to_outcome_index.get(record.cell_id, captured_horizon) < captured_horizon
                else None
            )
            if not matches_history_record(record, query, filters, outcome):
                continue
            if len(selected) == item_limit or remaining == 0:
                continuation = selected[-1].record_index + 1
                break
            excerpt = record.text[:remaining]
            selected.append(replace(record, text=excerpt, incomplete=record.incomplete or len(excerpt) < len(record.text)))
            if outcome is not None:
                cell_id_to_outcome[record.cell_id] = outcome
            remaining -= len(excerpt)
        return HistoryPage(
            tuple(selected),
            captured_horizon,
            continuation,
            continuation is not None or any(record.incomplete for record in selected),
            tuple(cell_id_to_outcome.items()),
        )

    def text(self, record_index: int) -> PlainTextObservation:
        require_operator("history_text")
        self._require_open()
        if record_index < 0 or record_index >= len(self._record_list):
            return PlainTextObservation("", False, True, "Record is unavailable")
        record = self._record_list[record_index]
        return PlainTextObservation(
            record.text,
            True,
            record.incomplete,
            "Original full text is unavailable; retained excerpt only" if record.incomplete else "Retained semantic text",
        )

    def _dispose(self) -> None:
        self._closed = True
        self._record_list.clear()
        self._cell_id_to_outcome.clear()
        self._cell_id_to_outcome_index.clear()
