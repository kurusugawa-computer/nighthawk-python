from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from nighthawk.harness.ledger import PerimeterEffectAttemptEvent, PerimeterEffectCompletionEvent


class PerimeterAuditRecord(BaseModel):
    """A perimeter-effect event handed to durable storage before ledger commit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    event_id: str
    parent_event_id: str | None
    event: PerimeterEffectAttemptEvent | PerimeterEffectCompletionEvent


@runtime_checkable
class PerimeterAuditStore(Protocol):
    """Durable storage for perimeter-effect attempt and completion events.

    ``record`` must persist the record before returning; raising prevents the
    perimeter effect from being attempted.
    """

    def record(self, record: PerimeterAuditRecord) -> None: ...
