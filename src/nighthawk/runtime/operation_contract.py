"""Live request and completion records for Natural operations."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import NighthawkError
from ..tools.contracts import ToolBoundaryError
from .execution_reference import ExecutionReference

type OperationKind = Literal["evaluation", "assignment", "return", "native_tool", "inspection", "output"]
type OperationPhase = Literal["admission", "inspection", "evaluation", "target_resolution", "validation", "assignment", "completed"]
type OperationStatus = Literal["succeeded", "rejected", "aborted", "skipped", "failed", "interrupted"]


class OperationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid", validate_default=True)

    execution_reference: ExecutionReference
    operation_id: str
    kind: OperationKind
    expression: str | None = None
    tool_name: str | None = None
    target_path: str | None = None
    argument_name_to_value: Mapping[str, object] = Field(default_factory=dict)
    expected_type: object = None

    @field_validator("argument_name_to_value", mode="after")
    @classmethod
    def preserve_live_arguments(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return MappingProxyType(dict(value))


@dataclass(frozen=True)
class OperationRejection:
    """One refused operation and the host's deliberately model-visible feedback."""

    request: OperationRequest
    reason: str
    guidance: str | None = None

    def model_feedback(self) -> dict[str, object]:
        """Expose correlation and feedback, without serializing live arguments or diagnostics."""
        return {
            "status": "rejected",
            "operation_id": self.request.operation_id,
            "step_execution_id": self.request.execution_reference.step_execution_id,
            "reason": self.reason,
            "guidance": self.guidance,
            "evaluation_started": False,
        }


class OperationRejectedError(NighthawkError):
    """The operation did not start; the same step can perform a different operation."""

    def __init__(self, rejection: OperationRejection) -> None:
        self.rejection = rejection
        super().__init__(rejection.reason)


@dataclass(frozen=True)
class OperationFinished:
    request: OperationRequest
    status: OperationStatus
    phase: OperationPhase
    evaluation_started: bool
    value: object = None
    original_exception: BaseException | None = None
    rejection: OperationRejection | None = None
    blocked_by_operation_id: str | None = None


class OperationError(ToolBoundaryError):
    """Recoverable runtime admission, lookup, or value-validation failure."""

    def __init__(self, *, message: str, phase: OperationPhase, evaluation_started: bool, guidance: str) -> None:
        self.phase = phase
        self.evaluation_started = evaluation_started
        super().__init__(
            kind="resolution" if phase in ("target_resolution", "evaluation") else "invalid_input",
            message=message,
            guidance=guidance,
        )


class OperationDeliveryError(NighthawkError):
    """A delivery failure preserving the already completed operation fact."""

    def __init__(self, finished: OperationFinished, delivery_cause: BaseException) -> None:
        self.finished = finished
        self.delivery_cause = delivery_cause
        super().__init__(f"Operation notification failed: {delivery_cause}")
