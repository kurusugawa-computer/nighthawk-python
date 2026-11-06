from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from opentelemetry.trace import get_current_span

from nighthawk.core.configuration import UNSET, UnsetType
from nighthawk.core.execution_reference import ExecutionReference
from nighthawk.core.operation_contract import OperationRequest
from nighthawk.core.scoping import RUN_ID, SCOPE_ID, SOURCE_LOCATION, STEP_EXECUTION_ID
from nighthawk.core.step_outcome import Break, Continue, OutcomeKind, Pass, Raise, Return, StepOutcome
from nighthawk.errors import NighthawkError


def _reference_mapping(name_to_value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(name_to_value))


class StepAbortedError(NighthawkError):
    """An explicit host decision ending the inspected step, without rollback."""

    def __init__(self, reason: str, *, subject: Literal["operation", "step_commit"]) -> None:
        self.reason = reason
        self.subject: Literal["operation", "step_commit"] = subject
        super().__init__(reason)


@dataclass(frozen=True)
class StepCommit:
    """Validated step result presented to ``Oversight.inspect_step_commit`` before it is committed.

    ``binding_name_to_value`` holds the write bindings after Pydantic validation and coercion.
    ``outcome`` is a resolved variant. Values retain type and identity in shallow
    read-only mapping views. Trusted hooks must not mutate these references; use
    Rewrite for changes and copy or serialize explicitly for durable history.
    """

    execution_reference: ExecutionReference
    processed_natural_program: str
    input_binding_name_to_value: Mapping[str, object]
    outcome: StepOutcome
    binding_name_to_value: Mapping[str, object]
    allowed_outcome_kinds: tuple[OutcomeKind, ...]
    output_binding_names: tuple[str, ...]
    binding_name_to_type: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_binding_name_to_value",
            _reference_mapping(self.input_binding_name_to_value),
        )
        object.__setattr__(
            self,
            "binding_name_to_value",
            _reference_mapping(self.binding_name_to_value),
        )
        object.__setattr__(self, "output_binding_names", tuple(self.output_binding_names))
        object.__setattr__(
            self,
            "binding_name_to_type",
            _reference_mapping(self.binding_name_to_type),
        )


@dataclass(frozen=True)
class Accept:
    reason: str | None = None


@dataclass(frozen=True)
class RejectOperation:
    """Do not execute this operation; expose reason and guidance to its caller or model."""

    reason: str
    guidance: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("RejectOperation requires a nonempty string reason")
        if self.guidance is not None and not isinstance(self.guidance, str):
            raise TypeError("RejectOperation guidance must be a string or None")


@dataclass(frozen=True)
class AbortStep:
    """End the current step; catching its exception cannot authorize completion."""

    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("AbortStep requires a nonempty string reason")


@dataclass(frozen=True)
class Rewrite:
    """Replace supplied commit fields; UNSET retains and Return(None) returns None.

    A mapping replaces all writes. Use outcome=Return(value=...) to supply
    a new return value or change another allowed outcome into a return.
    Replacements are validated before commit without replaying return expressions.
    A resolved Raise cannot be paired with an explicit nonempty output mapping.
    """

    outcome: StepOutcome | UnsetType = UNSET
    binding_name_to_value: Mapping[str, object] | UnsetType = UNSET
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, (UnsetType, Pass, Return, Break, Continue, Raise)):
            raise TypeError("Rewrite outcome must be a resolved StepOutcome or UNSET")
        if not isinstance(self.binding_name_to_value, UnsetType):
            if not isinstance(self.binding_name_to_value, Mapping) or any(not isinstance(name, str) for name in self.binding_name_to_value):
                raise TypeError("Rewrite binding_name_to_value must be a string-keyed mapping or UNSET")
            object.__setattr__(self, "binding_name_to_value", _reference_mapping(self.binding_name_to_value))
        if self.outcome is UNSET and self.binding_name_to_value is UNSET:
            raise ValueError("Rewrite must change outcome or binding_name_to_value")
        if isinstance(self.outcome, Raise) and not isinstance(self.binding_name_to_value, UnsetType) and len(self.binding_name_to_value):
            raise ValueError("Rewrite Raise cannot include output bindings")


type OperationDecision = Accept | RejectOperation | AbortStep
type StepCommitDecision = Accept | Rewrite | AbortStep


@dataclass(frozen=True, kw_only=True)
class Oversight:
    inspect_operation: Callable[[OperationRequest], OperationDecision | Awaitable[OperationDecision]] | None = None
    inspect_step_commit: Callable[[StepCommit], StepCommitDecision | Awaitable[StepCommitDecision]] | None = None


def record_oversight_decision(
    *,
    subject: str,
    verdict: str,
    execution_reference: ExecutionReference,
    tool_name: str | None = None,
    reason: str | None = None,
) -> None:
    current_span = get_current_span()
    if not current_span.is_recording():
        return
    if execution_reference.step_execution_id is None:
        raise NighthawkError("Oversight decision events require ExecutionReference.step_execution_id")

    attributes: dict[str, str] = {
        RUN_ID: execution_reference.run_id,
        SCOPE_ID: execution_reference.scope_id,
        STEP_EXECUTION_ID: execution_reference.step_execution_id,
        SOURCE_LOCATION: execution_reference.source_location or "",
        "nighthawk.oversight.subject": subject,
        "nighthawk.oversight.verdict": verdict,
    }
    if tool_name:
        attributes["tool.name"] = tool_name
    if reason:
        attributes["nighthawk.oversight.reason"] = reason

    current_span.add_event("nighthawk.oversight.decision", attributes)


__all__ = [
    "Accept",
    "AbortStep",
    "OperationDecision",
    "Oversight",
    "RejectOperation",
    "Rewrite",
    "StepAbortedError",
    "StepCommit",
    "StepCommitDecision",
]
