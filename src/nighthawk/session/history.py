from nighthawk.session._records import HistoryRecord
from nighthawk.session.operator_contract import HistoryFilter


def matches_history_record(record: HistoryRecord, query: str | None, filters: HistoryFilter, outcome: str | None) -> bool:
    """Shared literal matching over retained values and host-confirmed outcomes."""

    return (
        (query is None or query in record.text)
        and (filters.kind is None or filters.kind == record.kind)
        and (filters.cell_id is None or filters.cell_id == record.cell_id)
        and (filters.run_id is None or filters.run_id == record.run_id)
        and (filters.outcome is None or filters.outcome == outcome)
    )
