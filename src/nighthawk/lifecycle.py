"""Correlated lifecycle delivery for host-owned execution ledgers.

Records snapshot collection structure, retaining the exact live Python values.
Hosts own serialization, persistence, and deduplication by step_execution_id.
Hooks may complete directly or asynchronously.
Terminal delivery is attempted once, after caller binding assignments.
"""

import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from .errors import NighthawkError
from .runtime.execution_reference import ExecutionReference
from .runtime.operation_contract import OperationFinished
from .runtime.step_contract import StepKind
from .runtime.step_result import Break, Continue, Pass, Raise, Return, StepResult

type FailureStage = Literal[
    "preparation",
    "started_delivery",
    "operation_inspection",
    "operation_execution",
    "operation_delivery",
    "executor",
    "outcome_validation",
    "binding_validation",
    "return_await",
    "return_validation",
    "commit_inspection",
    "rewrite_validation",
    "oversight_rejection",
    "raise_construction",
    "binding_assignment",
]


@dataclass(frozen=True, kw_only=True)
class _StepFinished:
    execution_reference: ExecutionReference
    processed_natural_program: str | None = None
    input_binding_name_to_value: Mapping[str, object] | None = None
    allowed_step_kinds: tuple[StepKind, ...] | None = None
    assigned_binding_name_to_value: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.execution_reference.step_execution_id is None or self.execution_reference.source_location is None:
            raise ValueError("Terminal records require invocation and source identity")
        for name in ("input_binding_name_to_value", "assigned_binding_name_to_value", "validated_binding_name_to_value"):
            value = getattr(self, name, None)
            if value is not None:
                object.__setattr__(self, name, MappingProxyType(dict(value)))
        if self.allowed_step_kinds is not None:
            object.__setattr__(self, "allowed_step_kinds", tuple(self.allowed_step_kinds))


@dataclass(frozen=True, kw_only=True)
class StepCompleted(_StepFinished):
    """A resolved control result whose generated assignments completed."""

    outcome: Pass | Return | Break | Continue
    kind: Literal["completed"] = field(default="completed", init=False)


@dataclass(frozen=True, kw_only=True)
class StepRaised(_StepFinished):
    """An approved DSL Raise, with its successfully constructed exception."""

    outcome: Raise
    exception: BaseException
    kind: Literal["raised"] = field(default="raised", init=False)


@dataclass(frozen=True, kw_only=True)
class StepFailed(_StepFinished):
    """An ordinary execution failure, distinct from a DSL-selected Raise."""

    failure_stage: FailureStage
    original_exception: Exception
    attempted_outcome: StepResult | None = None
    validated_binding_name_to_value: Mapping[str, object] | None = None
    inspection_subject: Literal["step_commit", "operation"] | None = None
    rejection_reason: str | None = None
    kind: Literal["failed"] = field(default="failed", init=False)


@dataclass(frozen=True, kw_only=True)
class StepInterrupted(_StepFinished):
    """Cancellation or process control; propagation takes priority over delivery."""

    failure_stage: FailureStage
    original_exception: BaseException
    kind: Literal["interrupted"] = field(default="interrupted", init=False)


type StepFinished = StepCompleted | StepRaised | StepFailed | StepInterrupted


@dataclass(frozen=True)
class StepStarted:
    execution_reference: ExecutionReference


@dataclass(frozen=True)
class StepLifecycle:
    """Scoped notifications, inherited with UNSET and cleared by None.

    A callback may persist a record and raise a host exception referencing it.
    Callback errors never trigger another execution notification or rollback.
    Rethrowing the exact original exception preserves its existing cause.
    Host deduplication must match both execution identity and the stored event.
    """

    on_step_finished: Callable[[StepFinished], None | Awaitable[None]] | None = None
    on_operation_finished: Callable[[OperationFinished], None | Awaitable[None]] | None = None
    on_step_started: Callable[[StepStarted], None | Awaitable[None]] | None = None
    interruption_delivery_timeout: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.interruption_delivery_timeout) or self.interruption_delivery_timeout <= 0:
            raise ValueError("interruption_delivery_timeout must be finite and positive")


class StepDeliveryError(NighthawkError):
    """Host adapter delivery failure; does not assert that persistence succeeded."""

    def __init__(self, execution_reference: ExecutionReference, cause: BaseException) -> None:
        self.execution_reference = execution_reference
        self.delivery_cause = cause
        super().__init__(f"Step terminal delivery failed: {cause}")
        self.__cause__ = cause


__all__ = [
    "FailureStage",
    "StepCompleted",
    "StepDeliveryError",
    "StepFailed",
    "StepFinished",
    "StepInterrupted",
    "StepLifecycle",
    "StepRaised",
    "StepStarted",
]
