from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from opentelemetry.trace import get_current_span

from .composition import UNSET, UnsetType
from .errors import NighthawkError
from .runtime.execution_reference import ExecutionReference
from .runtime.operation_contract import OperationRequest
from .runtime.step_contract import StepKind
from .runtime.step_result import Break, Continue, Pass, Raise, Return, StepResult


def _reference_mapping(name_to_value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(name_to_value))


class StepAbortedError(NighthawkError):
    """An explicit host decision ending the inspected step, without rollback."""

    def __init__(self, reason: str, *, subject: str = "operation") -> None:
        self.reason = reason
        self.subject = subject
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
    outcome: StepResult
    binding_name_to_value: Mapping[str, object]
    allowed_step_kinds: tuple[StepKind, ...]
    output_binding_name_set: frozenset[str]
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
        object.__setattr__(
            self,
            "output_binding_name_set",
            frozenset(self.output_binding_name_set),
        )
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

    outcome: StepResult | UnsetType = UNSET
    binding_name_to_value: Mapping[str, object] | UnsetType = UNSET
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, (UnsetType, Pass, Return, Break, Continue, Raise)):
            raise TypeError("Rewrite outcome must be a resolved StepResult or UNSET")
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


@dataclass(frozen=True)
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
        "run.id": execution_reference.run_id,
        "scope.id": execution_reference.scope_id,
        "step.execution.id": execution_reference.step_execution_id,
        "step.source_location": execution_reference.source_location or "",
        "nighthawk.oversight.subject": subject,
        "nighthawk.oversight.verdict": verdict,
    }
    if tool_name:
        attributes["tool.name"] = tool_name
    if reason:
        attributes["nighthawk.oversight.reason"] = reason

    current_span.add_event("nighthawk.oversight.decision", attributes)


__all__ = [
    "Pass",
    "Return",
    "Break",
    "Continue",
    "Raise",
    "StepResult",
    "Accept",
    "AbortStep",
    "OperationDecision",
    "Oversight",
    "RejectOperation",
    "StepAbortedError",
    "Rewrite",
    "StepCommit",
    "StepCommitDecision",
]
