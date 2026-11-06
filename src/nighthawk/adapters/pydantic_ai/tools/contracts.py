from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from nighthawk.core.operation_contract import OperationFinished, OperationRejection

type ErrorKind = Literal["invalid_input", "resolution", "oversight", "exception"]

_MAXIMUM_EXCEPTION_TEXT_LENGTH = 2000


class ToolError(TypedDict):
    kind: ErrorKind
    message: str
    guidance: str | None
    phase: NotRequired[str]
    evaluation_started: NotRequired[bool]
    operation_id: NotRequired[str]
    step_execution_id: NotRequired[str | None]
    status: NotRequired[Literal["rejected", "skipped", "failed"]]


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


def exception_feedback(finished: OperationFinished) -> ToolError:
    """Report a failed operation's exception type and bounded message, never its traceback or live values."""
    exception = finished.original_exception
    assert exception is not None
    text = f"{type(exception).__name__}: {exception}"
    if len(text) > _MAXIMUM_EXCEPTION_TEXT_LENGTH:
        text = text[:_MAXIMUM_EXCEPTION_TEXT_LENGTH] + " [truncated]"
    return {
        "kind": "exception",
        "status": "failed",
        "operation_id": finished.request.operation_id,
        "step_execution_id": finished.request.execution_reference.step_execution_id,
        "message": text,
        "guidance": (
            "The operation raised this exception; effects completed before it remain, so do not repeat them blindly. "
            if finished.evaluation_started
            else "The operation was refused by this exception before evaluation started. "
        )
        + "Correct the cause and continue, or, when raise is allowed, end the step with raise and this "
        "raise_operation_id to propagate the original exception.",
        "phase": finished.phase,
        "evaluation_started": finished.evaluation_started,
    }
