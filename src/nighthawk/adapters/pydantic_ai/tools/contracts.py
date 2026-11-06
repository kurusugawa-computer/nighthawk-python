from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from nighthawk.core.operation_contract import OperationRejection

type ErrorKind = Literal["invalid_input", "resolution", "oversight"]


class ToolError(TypedDict):
    kind: ErrorKind
    message: str
    guidance: str | None
    phase: NotRequired[str]
    evaluation_started: NotRequired[bool]
    operation_id: NotRequired[str]
    step_execution_id: NotRequired[str | None]
    status: NotRequired[Literal["rejected", "skipped"]]


class ToolOutcome(TypedDict):
    payload: object | None
    error: ToolError | None


class ToolResultObservation(TypedDict):
    """Carries the canonical ``ToolOutcome``; backend boundaries derive text previews and trace payloads lazily."""

    kind: Literal["tool_result"]
    tool_outcome: ToolOutcome


class RetryPromptObservation(TypedDict):
    kind: Literal["retry_prompt"]
    retry_text: str


type ToolHandlerResult = ToolResultObservation | RetryPromptObservation


def rejection_feedback(rejection: OperationRejection) -> ToolError:
    """Project deliberate correction guidance without exposing live arguments."""
    return {
        "kind": "oversight",
        "status": "rejected",
        "operation_id": rejection.request.operation_id,
        "step_execution_id": rejection.request.execution_reference.step_execution_id,
        "message": rejection.reason,
        "guidance": rejection.guidance,
        "phase": "inspection",
        "evaluation_started": False,
    }
