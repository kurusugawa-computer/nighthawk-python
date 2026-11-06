"""Session cells and shared conversation values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .._conversation_records import (
    BasicDataValue,
    CellStatus,
    ContextItem,
    ContextPage,
    ContextQuery,
    ConversationHistoryQuery,
    ConversationInput,
    ConversationPage,
    ConversationRecord,
    ConversationResult,
    DataExport,
    FunctionDeclaration,
    FunctionSelection,
    SourceCandidate,
    SourceExport,
    SourceInvocation,
    SourceRetention,
    UserFacingBlock,
)
from ..history import EvidenceReference

__all__ = [
    "BasicDataValue",
    "CellInput",
    "CellMode",
    "CellOutcome",
    "CellOutput",
    "CellOutputPage",
    "CellStatus",
    "ContextItem",
    "ContextPage",
    "ContextQuery",
    "ConversationHistoryQuery",
    "ConversationInput",
    "ConversationPage",
    "ConversationRecord",
    "ConversationResult",
    "DataExport",
    "FunctionDeclaration",
    "FunctionSelection",
    "SourceCandidate",
    "SourceExport",
    "SourceInvocation",
    "SourceRetention",
    "UserFacingBlock",
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
