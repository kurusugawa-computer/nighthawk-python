from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from nighthawk.harness.perimeter import EffectFootprint


class ConfinementRequest(BaseModel):
    """What the host asks the platform to confirm before a dispatch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    submitting_node_id: str
    recursion_depth: int = Field(ge=0)
    effect_footprint: EffectFootprint


class ConfinementConfirmation(BaseModel):
    """Platform confirmation that generated execution will be confined."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    confinement_identity: str


@runtime_checkable
class PerimeterConfinement(Protocol):
    """Platform-supplied preventive boundary for generated execution.

    ``confirm`` returns a confirmation when the platform establishes that direct
    perimeter effects are prevented for the dispatch, or ``None`` when it cannot;
    the host then rejects the dispatch before generated execution begins.
    """

    def confirm(self, request: ConfinementRequest) -> ConfinementConfirmation | None: ...


class _UnconfirmedPerimeterConfinement:
    def confirm(self, request: ConfinementRequest) -> ConfinementConfirmation | None:
        del request
        return None


def unconfirmed_perimeter_confinement() -> PerimeterConfinement:
    """Return a confinement contract that never confirms, so every dispatch is rejected."""

    return _UnconfirmedPerimeterConfinement()
