"""Session cell and history records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nighthawk.harness.evidence import EvidenceReference

__all__ = [
    "CellInput",
    "CellMode",
    "CellOutcome",
    "CellOutput",
    "CellOutputPage",
    "CellStatus",
    "HistoryPage",
    "HistoryRecord",
]

type CellMode = Literal["python", "natural_language"]


@dataclass(frozen=True)
class CellInput:
    text: str
    mode: CellMode | None = None


@dataclass(frozen=True)
class CellOutput:
    cell_id: str
    kind: str
    text: str
    run_id: str | None = None
    event_id: str | None = None
    incomplete: bool = False
    evidence_reference_tuple: tuple[EvidenceReference, ...] = ()


@dataclass(frozen=True)
class CellOutputPage:
    output_tuple: tuple[CellOutput, ...]
    next_starting_index: int | None
    incomplete: bool


@dataclass(frozen=True)
class CellOutcome:
    status: CellStatus
    cell_id: str
    output_tuple: tuple[CellOutput, ...]
    run_id_tuple: tuple[str, ...]


type CellStatus = Literal["queued", "running", "interruption_requested", "success", "failure", "cancelled"]


@dataclass(frozen=True)
class HistoryRecord:
    record_index: int
    cell_id: str
    kind: str
    text: str
    run_id: str | None = None
    event_id: str | None = None
    incomplete: bool = False


@dataclass(frozen=True)
class HistoryPage:
    record_tuple: tuple[HistoryRecord, ...]
    horizon: int
    next_starting_index: int | None
    incomplete: bool
    # Confirmed outcomes for returned cells, bounded by this page's horizon.
    cell_outcome_tuple: tuple[tuple[str, CellStatus], ...] = ()
