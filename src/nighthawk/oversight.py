from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from opentelemetry.trace import get_current_span
from pydantic import BaseModel

from .errors import NighthawkError
from .runtime.scoping import ExecutionRef
from .runtime.step_contract import StepKind, StepOutcome


def _snapshot_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_copy(deep=True)
    if isinstance(value, Mapping):
        return MappingProxyType({key: _snapshot_value(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(_snapshot_value(item) for item in value)
    if isinstance(value, list):
        return tuple(_snapshot_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_snapshot_value(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_snapshot_value(item) for item in value)
    return value


def _snapshot_mapping(name_to_value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({name: _snapshot_value(value) for name, value in name_to_value.items()})


def _snapshot_optional_step_outcome(step_outcome: StepOutcome | None) -> StepOutcome | None:
    if step_outcome is None:
        return None
    return _snapshot_value(step_outcome)  # type: ignore[return-value]


class OversightRejectedError(NighthawkError):
    """Raised when oversight rejects a tool call or step commit."""


@dataclass(frozen=True)
class ToolCall:
    execution_ref: ExecutionRef
    tool_name: str
    argument_name_to_value: Mapping[str, object]
    processed_natural_program: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "argument_name_to_value",
            _snapshot_mapping(self.argument_name_to_value),
        )


@dataclass(frozen=True)
class StepCommit:
    """Validated step result presented to ``Oversight.inspect_step_commit`` before it is committed.

    ``binding_name_to_value`` holds the write bindings after Pydantic validation and coercion.
    ``return_value`` holds the resolved and validated return value when ``step_outcome.kind == "return"``; otherwise ``None``.
    """

    execution_ref: ExecutionRef
    processed_natural_program: str
    input_binding_name_to_value: Mapping[str, object]
    step_outcome: StepOutcome
    binding_name_to_value: Mapping[str, object]
    return_value: object | None
    allowed_step_kinds: tuple[StepKind, ...]
    output_binding_name_set: frozenset[str]
    binding_name_to_type: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_binding_name_to_value",
            _snapshot_mapping(self.input_binding_name_to_value),
        )
        object.__setattr__(
            self,
            "step_outcome",
            _snapshot_value(self.step_outcome),
        )
        object.__setattr__(
            self,
            "binding_name_to_value",
            _snapshot_mapping(self.binding_name_to_value),
        )
        object.__setattr__(
            self,
            "return_value",
            _snapshot_value(self.return_value),
        )
        object.__setattr__(
            self,
            "output_binding_name_set",
            frozenset(self.output_binding_name_set),
        )
        object.__setattr__(
            self,
            "binding_name_to_type",
            _snapshot_mapping(self.binding_name_to_type),
        )


@dataclass(frozen=True)
class Accept:
    reason: str | None = None


@dataclass(frozen=True)
class Reject:
    reason: str


@dataclass(frozen=True)
class Rewrite:
    """Replace parts of a step commit. Rewritten values are validated again before commit.

    ``binding_name_to_value`` replaces the whole committed mapping. ``return_value`` is honored only when the effective
    step outcome kind is ``return``; it bypasses ``return_expression`` evaluation and is validated against the return annotation.
    """

    step_outcome: StepOutcome | None = None
    binding_name_to_value: Mapping[str, object] | None = None
    return_value: object | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "step_outcome",
            _snapshot_optional_step_outcome(self.step_outcome),
        )
        if self.binding_name_to_value is not None:
            object.__setattr__(
                self,
                "binding_name_to_value",
                _snapshot_mapping(self.binding_name_to_value),
            )
        object.__setattr__(
            self,
            "return_value",
            _snapshot_value(self.return_value),
        )
        if self.step_outcome is None and self.binding_name_to_value is None and self.return_value is None:
            raise ValueError("Rewrite must change step_outcome, binding_name_to_value, or return_value")


type ToolCallDecision = Accept | Reject
type StepCommitDecision = Accept | Reject | Rewrite


@dataclass(frozen=True)
class Oversight:
    inspect_tool_call: Callable[[ToolCall], ToolCallDecision] | None = None
    inspect_step_commit: Callable[[StepCommit], StepCommitDecision] | None = None


def record_oversight_decision(
    *,
    subject: str,
    verdict: str,
    execution_ref: ExecutionRef,
    tool_name: str | None = None,
    reason: str | None = None,
) -> None:
    current_span = get_current_span()
    if not current_span.is_recording():
        return
    if execution_ref.step_id is None:
        raise NighthawkError("Oversight decision events require ExecutionRef.step_id")

    attributes: dict[str, str] = {
        "run.id": execution_ref.run_id,
        "scope.id": execution_ref.scope_id,
        "step.id": execution_ref.step_id,
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
    "Oversight",
    "OversightRejectedError",
    "Reject",
    "Rewrite",
    "StepCommit",
    "StepCommitDecision",
    "ToolCall",
    "ToolCallDecision",
]
