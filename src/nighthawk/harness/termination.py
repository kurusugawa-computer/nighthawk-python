from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class TerminationRequest(BaseModel):
    """Forced termination of a dispatch branch and its descendants."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    requesting_node_id: str
    target_node_id: str
    affected_node_id_tuple: tuple[str, ...]


class TerminationReport(BaseModel):
    """Whether the platform established that the targeted branches ceased."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ceased: bool
    detail: str = ""


@runtime_checkable
class TerminationMechanism(Protocol):
    """Platform mechanism that stops branches without their cooperation."""

    def terminate(self, request: TerminationRequest) -> TerminationReport: ...


class _UnavailableTerminationMechanism:
    def terminate(self, request: TerminationRequest) -> TerminationReport:
        del request
        return TerminationReport(ceased=False, detail="no termination mechanism is configured")


def unavailable_termination_mechanism() -> TerminationMechanism:
    """Return a termination mechanism that never establishes cessation."""

    return _UnavailableTerminationMechanism()


@dataclass(frozen=True)
class TerminationCompletion:
    """Typed completion returned to a parent after descendant termination."""

    target_node_id: str
    affected_node_id_tuple: tuple[str, ...]
    completion_event_id: str
