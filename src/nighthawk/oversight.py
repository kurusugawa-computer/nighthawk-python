from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from opentelemetry.trace import get_current_span

from .composition import UNSET, UnsetType
from .errors import NighthawkError
from .runtime.execution_reference import ExecutionReference
from .runtime.step_contract import StepKind
from .runtime.step_result import Break, Continue, Pass, Raise, Return, StepResult


def _reference_mapping(name_to_value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(name_to_value))


class OversightRejectedError(NighthawkError):
    """Raised when oversight explicitly rejects an execution boundary."""

    def __init__(self, reason: str, *, subject: str = "tool_call") -> None:
        self.reason = reason
        self.subject = subject
        super().__init__(reason)


@dataclass(frozen=True)
class ToolCall:
    execution_reference: ExecutionReference
    tool_name: str
    argument_name_to_value: Mapping[str, object]
    processed_natural_program: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "argument_name_to_value",
            _reference_mapping(self.argument_name_to_value),
        )


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
class ReturnExpression:
    """Trusted expression approval before core evaluation, await, and validation."""

    execution_reference: ExecutionReference
    expression: str
    expected_type: object
    processed_natural_program: str
    validated_binding_name_to_value: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "validated_binding_name_to_value", _reference_mapping(self.validated_binding_name_to_value))


@dataclass(frozen=True)
class Accept:
    reason: str | None = None


@dataclass(frozen=True)
class Reject:
    reason: str


@dataclass(frozen=True)
class Rewrite:
    """Replace supplied commit fields; UNSET inherits and explicit None is a return.

    A mapping replaces all writes. A return_value patch requires an existing Return.
    Use outcome=Return(value=...) to change another allowed outcome into a return.
    Replacements are validated before commit without replaying return expressions.
    """

    outcome: StepResult | UnsetType = UNSET
    binding_name_to_value: Mapping[str, object] | UnsetType = UNSET
    return_value: object = UNSET
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, (UnsetType, Pass, Return, Break, Continue, Raise)):
            raise TypeError("Rewrite outcome must be a resolved StepResult or UNSET")
        if self.outcome is not UNSET and self.return_value is not UNSET:
            raise ValueError("Rewrite cannot supply both outcome and return_value")
        if not isinstance(self.binding_name_to_value, UnsetType):
            if not isinstance(self.binding_name_to_value, Mapping):
                raise TypeError("Rewrite binding_name_to_value must be a mapping or UNSET")
            object.__setattr__(self, "binding_name_to_value", _reference_mapping(self.binding_name_to_value))
        if self.outcome is UNSET and self.binding_name_to_value is UNSET and self.return_value is UNSET:
            raise ValueError("Rewrite must change outcome, binding_name_to_value, or return_value")


type ToolCallDecision = Accept | Reject
type StepCommitDecision = Accept | Reject | Rewrite


@dataclass(frozen=True)
class Oversight:
    inspect_return_expression: Callable[[ReturnExpression], Accept | Reject] | None = None
    inspect_tool_call: Callable[[ToolCall], ToolCallDecision] | None = None
    inspect_step_commit: Callable[[StepCommit], StepCommitDecision] | None = None


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
    "Oversight",
    "OversightRejectedError",
    "Reject",
    "Rewrite",
    "StepCommit",
    "ReturnExpression",
    "StepCommitDecision",
    "ToolCall",
    "ToolCallDecision",
]
