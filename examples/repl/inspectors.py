"""Search retained observations while keeping stable record identities and return positions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nighthawk.session import HistoryFilter, HistoryRecord
from nighthawk.session.history import matches_history_record


@dataclass(frozen=True)
class InspectorLocation:
    view: str
    query: str
    record_index: int | None
    position: int
    detail_text: str = ""
    run_id: str | None = None


class HistoryInspector:
    def __init__(self) -> None:
        self.query = ""
        self.filters = HistoryFilter()
        self.selected_record_index: int | None = None
        self.return_location_list: list[InspectorLocation] = []

    def rows(self, records: tuple[HistoryRecord, ...], cell_id_to_outcome: Mapping[str, str] | None = None) -> tuple[HistoryRecord, ...]:
        return tuple(
            record
            for record in records
            if matches_history_record(
                record, self.query, self.filters, cell_id_to_outcome.get(record.cell_id) if cell_id_to_outcome is not None else None
            )
        )

    def position(self, rows: tuple[HistoryRecord, ...]) -> int:
        return next((i for i, record in enumerate(rows) if record.record_index == self.selected_record_index), 0)

    def select(self, rows: tuple[HistoryRecord, ...], position: int) -> HistoryRecord | None:
        if not rows:
            return None
        record = rows[min(max(position, 0), len(rows) - 1)]
        self.selected_record_index = record.record_index
        return record
