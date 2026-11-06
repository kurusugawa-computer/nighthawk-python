"""Typed JSON transport for the example terminal; live Python values never cross it."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from nighthawk.harness.evidence import EvidenceReference
from nighthawk.session import (
    BackgroundObservation,
    ExecutionObservation,
    HistoryPage,
    LocalPage,
    OperationDescriptor,
    OperatorRequest,
    OperatorResult,
)


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SubmitRequest(Message):
    kind: Literal["submit"] = "submit"
    request_id: str
    text: str
    mode: Literal["python", "natural_language"] | None = None


class InterruptRequest(Message):
    kind: Literal["interrupt"] = "interrupt"
    request_id: str
    cell_id: str


class ControlRequest(Message):
    kind: Literal["control"] = "control"
    request_id: str
    operation: Literal["observe", "close"]


class OperationRequest(Message):
    kind: Literal["operation"] = "operation"
    request_id: str
    request: Annotated[OperatorRequest, Field(discriminator="operation")]
    cell_id: str | None = None


Request = Annotated[SubmitRequest | InterruptRequest | ControlRequest | OperationRequest, Field(discriminator="kind")]
REQUEST_ADAPTER: TypeAdapter[Request] = TypeAdapter(Request)


class Observation(Message):
    kind: Literal["observation"] = "observation"
    revision: int
    observed_at: float
    cell_id: str | None = None
    state: str
    started_at: float | None = None
    run_id_tuple: tuple[str, ...] = ()
    binding_name_tuple: tuple[str, ...] = ()
    execution: ExecutionObservation | None = None
    locals: LocalPage | None = None
    background_tuple: tuple[BackgroundObservation, ...] = ()
    operation_tuple: tuple[OperationDescriptor, ...] = ()
    history: HistoryPage | None = None


class Ready(Message):
    kind: Literal["ready"] = "ready"
    process_id: int
    session_id: str
    observation: Observation


class Response(Message):
    kind: Literal["response"] = "response"
    request_id: str
    status: Literal["submitted", "requested", "finished", "missing", "busy", "observed", "closed", "failed", "refused", "accepted"]
    cell_id: str | None = None
    detail: str = ""
    observation: Observation | None = None
    output_sequence: int | None = None


class OutputRecord(Message):
    kind: Literal["output"] = "output"
    sequence: int
    cell_id: str | None
    category: Literal["stdout", "stderr", "semantic"]
    text: str
    semantic_kind: str | None = None
    run_id: str | None = None
    event_id: str | None = None
    incomplete: bool = False
    evidence_reference_tuple: tuple[EvidenceReference, ...] = ()


class OperationResponse(Message):
    kind: Literal["operation_result"] = "operation_result"
    request_id: str
    result: OperatorResult
    observation: Observation | None = None


class Closed(Message):
    kind: Literal["closed"] = "closed"
    output_sequence: int


class Completed(Message):
    kind: Literal["completed"] = "completed"
    cell_id: str
    status: str
    observation: Observation
    output_tuple: tuple[OutputRecord, ...]
    output_incomplete: bool = False


class Failure(Message):
    kind: Literal["failure"] = "failure"
    phase: Literal["startup", "transport", "execution"]
    detail: str
    output_sequence: int | None = None


Event = Annotated[Ready | Response | Observation | OutputRecord | Completed | Failure | OperationResponse | Closed, Field(discriminator="kind")]
EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)
