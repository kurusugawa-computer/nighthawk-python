"""Application-owned SQLite perimeter audit storage.

SQLite commits with synchronous=FULL before record() returns.
Durability relies on SQLite's supported local filesystem and honest storage synchronization;
this example does not establish remote replication, tamper resistance, or source approval.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from nighthawk.agent import PerimeterEffectAttemptEvent, PerimeterEffectCompletionEvent
from nighthawk.agent.runtime import PerimeterAuditRecord


class SqlitePerimeterAuditStore:
    """Persist append-only identified observations, opening a connection per operation.

    The application selects an existing writable directory and controls its retention policy.
    Concurrent callers use SQLite's transaction serialization. A duplicate event identity
    is refused; no record is overwritten. This store is configured outside agent runs.
    """

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS perimeter_events (sequence INTEGER PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(run_id, event_id))"
            )

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def record(self, record: PerimeterAuditRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO perimeter_events (run_id, event_id, record) VALUES (?, ?, ?)",
                (record.run_id, record.event_id, record.model_dump_json()),
            )

    def read(self, run_id: str) -> tuple[PerimeterAuditRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute("SELECT record FROM perimeter_events WHERE run_id = ? ORDER BY sequence", (run_id,)).fetchall()
        return tuple(PerimeterAuditRecord.model_validate_json(row[0]) for row in rows)

    def unmatched_attempts(self, run_id: str) -> tuple[PerimeterAuditRecord, ...]:
        """Return attempts whose durable completion remains indeterminate."""
        records = self.read(run_id)
        completed = {record.event.attempt_event_id for record in records if isinstance(record.event, PerimeterEffectCompletionEvent)}
        return tuple(record for record in records if isinstance(record.event, PerimeterEffectAttemptEvent) and record.event_id not in completed)
