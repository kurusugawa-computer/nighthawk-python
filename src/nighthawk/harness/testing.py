from __future__ import annotations

import threading

from nighthawk.harness.audit import PerimeterAuditRecord


class InMemoryPerimeterAuditStore:
    """Process-local perimeter audit store.

    This store does not satisfy the durability obligation: its records vanish
    with the process. Use it for tests and local experiments only.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._record_list: list[PerimeterAuditRecord] = []

    def record(self, record: PerimeterAuditRecord) -> None:
        with self._lock:
            self._record_list.append(record)

    @property
    def records(self) -> tuple[PerimeterAuditRecord, ...]:
        with self._lock:
            return tuple(self._record_list)
