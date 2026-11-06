"""Immutable operator observations and explicitly typed local control requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..history import HistoryBounds, HistoryProjection, HistoryQuery
from ._records import ConversationPage

type Exposure = Literal["candidate", "private", "controller", "unavailable"]


@dataclass(frozen=True)
class LocalQuery:
    name: str = ""
    type_description: str = ""
    exposure: Exposure | None = None
    starting_index: int = 0
    maximum_item_count: int = 20
    maximum_character_count: int = 6000
    revision: int | None = None


@dataclass(frozen=True)
class LocalBindingObservation:
    name: str
    type_description: str
    preview: str
    exposure: Exposure
    exposure_description: str
    observed_at: float
    revision: int
    incomplete: bool = False
    available: bool = True


@dataclass(frozen=True)
class LocalPage:
    binding_tuple: tuple[LocalBindingObservation, ...]
    observed_at: float
    revision: int
    next_starting_index: int | None = None
    incomplete: bool = False
    unavailable_reason: str | None = None
    session_name_shadowed: bool = False


@dataclass(frozen=True)
class BindingMutationResult:
    status: Literal["applied", "busy", "missing", "collision", "unavailable"]
    name: str
    new_name: str | None
    revision: int
    reason: str = ""


@dataclass(frozen=True)
class ExecutionObservation:
    cell_id: str | None
    run_id_tuple: tuple[str, ...]
    state: Literal["idle", "queued", "running", "interruption_requested", "success", "failure", "cancelled", "closed"]
    activity: str
    started_at: float | None
    observed_at: float
    revision: int
    interruption_available: bool
    unknown_reason: str | None = None


@dataclass(frozen=True)
class InterruptionResult:
    status: Literal["requested", "already_finished", "unavailable", "missing"]
    target_id: str | None
    reason: str = ""


@dataclass(frozen=True)
class BackgroundObservation:
    work_id: str
    owner: str
    description: str
    state: Literal["running", "interruption_requested", "stopped", "failed", "unknown"]
    observed_at: float
    interruption_available: bool
    cell_id: str | None = None
    run_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class ExitResult:
    status: Literal["refused", "accepted", "closed"]
    blocker_tuple: tuple[BackgroundObservation, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class OperationParameter:
    name: str
    type_description: str
    required: bool = False
    default: str = ""


@dataclass(frozen=True)
class OperationDescriptor:
    name: str
    signature: str
    description: str
    parameter_tuple: tuple[OperationParameter, ...]
    kind: Literal["query", "mutation", "lifecycle"]
    available: bool = True
    unavailable_reason: str = ""


@dataclass(frozen=True)
class HistoryFilter:
    kind: str | None = None
    cell_id: str | None = None
    run_id: str | None = None
    outcome: Literal["success", "failure", "cancelled"] | None = None


@dataclass(frozen=True)
class PlainTextObservation:
    text: str
    available: bool
    incomplete: bool
    description: str = ""


@dataclass(frozen=True)
class ReadLocalsRequest:
    query: LocalQuery = LocalQuery()
    operation: Literal["session.locals.read"] = "session.locals.read"


@dataclass(frozen=True)
class InspectLocalRequest:
    name: str
    operation: Literal["session.locals.inspect"] = "session.locals.inspect"


@dataclass(frozen=True)
class DeleteLocalRequest:
    name: str
    operation: Literal["session.locals.delete"] = "session.locals.delete"


@dataclass(frozen=True)
class RenameLocalRequest:
    name: str
    new_name: str
    operation: Literal["session.locals.rename"] = "session.locals.rename"


@dataclass(frozen=True)
class ReadExecutionRequest:
    operation: Literal["session.execution.read"] = "session.execution.read"


@dataclass(frozen=True)
class InterruptExecutionRequest:
    operation: Literal["session.execution.interrupt"] = "session.execution.interrupt"


@dataclass(frozen=True)
class ListBackgroundRequest:
    operation: Literal["session.background.list"] = "session.background.list"


@dataclass(frozen=True)
class RegisterBackgroundRequest:
    binding_name: str
    operation: Literal["session.background.register"] = "session.background.register"


@dataclass(frozen=True)
class InterruptBackgroundRequest:
    work_id: str
    operation: Literal["session.background.interrupt"] = "session.background.interrupt"


@dataclass(frozen=True)
class ExitRequest:
    operation: Literal["session.exit"] = "session.exit"


@dataclass(frozen=True)
class HelpRequest:
    operation_name: str | None = None
    operation: Literal["session.help"] = "session.help"


@dataclass(frozen=True)
class ListOperationsRequest:
    query: str = ""
    operation: Literal["session.operations.list"] = "session.operations.list"


@dataclass(frozen=True)
class ReadHistoryRequest:
    starting_index: int = 0
    horizon: int | None = None
    maximum_item_count: int = 20
    maximum_character_count: int = 6000
    filters: HistoryFilter = HistoryFilter()
    operation: Literal["session.history.read"] = "session.history.read"


@dataclass(frozen=True)
class SearchHistoryRequest:
    query: str
    starting_index: int = 0
    horizon: int | None = None
    maximum_item_count: int = 20
    maximum_character_count: int = 6000
    filters: HistoryFilter = HistoryFilter()
    operation: Literal["session.history.search"] = "session.history.search"


@dataclass(frozen=True)
class ReadEvidenceRequest:
    run_id: str
    query: HistoryQuery = field(default_factory=HistoryQuery)
    bounds: HistoryBounds = field(default_factory=HistoryBounds)
    operation: Literal["session.read_run_evidence"] = "session.read_run_evidence"


@dataclass(frozen=True)
class ReadTextRequest:
    record_index: int
    operation: Literal["session.history.text"] = "session.history.text"


type OperatorRequest = (
    ReadLocalsRequest
    | InspectLocalRequest
    | DeleteLocalRequest
    | RenameLocalRequest
    | ReadExecutionRequest
    | InterruptExecutionRequest
    | ListBackgroundRequest
    | RegisterBackgroundRequest
    | InterruptBackgroundRequest
    | ExitRequest
    | HelpRequest
    | ListOperationsRequest
    | ReadHistoryRequest
    | SearchHistoryRequest
    | ReadEvidenceRequest
    | ReadTextRequest
)
type OperatorResult = (
    str
    | LocalPage
    | LocalBindingObservation
    | BindingMutationResult
    | ExecutionObservation
    | InterruptionResult
    | tuple[BackgroundObservation, ...]
    | ExitResult
    | tuple[OperationDescriptor, ...]
    | ConversationPage
    | HistoryProjection
    | PlainTextObservation
)
