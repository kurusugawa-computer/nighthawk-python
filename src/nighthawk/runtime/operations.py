"""One serialized admission, inspection, execution, and notification pipeline."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, ModelRetry

from ..errors import NighthawkError
from ..oversight import AbortStep, Accept, RejectOperation, StepAbortedError, record_oversight_decision
from ..ulid import generate_ulid
from .interruption import chain_cleanup_errors
from .notifications import await_notification
from .operation_contract import (
    OperationDeliveryError,
    OperationError,
    OperationFinished,
    OperationKind,
    OperationPhase,
    OperationRejectedError,
    OperationRejection,
    OperationRequest,
    OperationStatus,
)
from .step_context import StepContext

_active_operation_context: ContextVar[StepContext | None] = ContextVar("nighthawk_active_operation_context", default=None)


@dataclass
class OperationExecution:
    request: OperationRequest
    phase: OperationPhase = "admission"
    evaluation_started: bool = False


async def execute_operation_async(
    step_context: StepContext,
    request: OperationRequest,
    *,
    admit: Callable[[], None],
    execute: Callable[[OperationExecution], Awaitable[object]],
    recoverable_admission_error: BaseException | None = None,
    blocked_by: OperationRejection | None = None,
) -> object:
    from .scoping import _lifecycle_var, _oversight_var

    if _active_operation_context.get() is step_context:
        raise NighthawkError("A runtime operation cannot await another operation on its own step; use ordinary Python composition")
    async with step_context.operation_lock:
        if step_context.operation_failure is not None:
            raise step_context.operation_failure
        execution = OperationExecution(request)
        token = _active_operation_context.set(step_context)
        outcome: object = None
        original_exception: BaseException | None = None
        status: OperationStatus = "succeeded"
        rejection_error: OperationRejectedError | None = None
        abort_error: StepAbortedError | None = None
        try:
            try:
                if blocked_by is not None:
                    status = "skipped"
                else:
                    admit()
                execution.phase = "inspection"
                oversight = _oversight_var.get()
                if blocked_by is None and oversight is not None and oversight.inspect_operation is not None:
                    decision = oversight.inspect_operation(request)
                    if inspect.isawaitable(decision):
                        decision = await decision
                    if not isinstance(decision, (Accept, RejectOperation, AbortStep)):
                        raise NighthawkError("Operation inspection must return Accept, RejectOperation, or AbortStep")
                    record_oversight_decision(
                        subject="operation",
                        verdict="reject" if isinstance(decision, RejectOperation) else "abort" if isinstance(decision, AbortStep) else "accept",
                        execution_reference=request.execution_reference,
                        tool_name=request.tool_name,
                        reason=decision.reason,
                    )
                    if isinstance(decision, RejectOperation):
                        rejection_error = OperationRejectedError(OperationRejection(request, decision.reason, decision.guidance))
                        raise rejection_error
                    if isinstance(decision, AbortStep):
                        abort_error = StepAbortedError(decision.reason, subject="operation")
                        step_context.operation_abort = abort_error
                        raise abort_error
                if blocked_by is None:
                    execution.phase = "evaluation"
                    execution.evaluation_started = True
                    outcome = await execute(execution)
                    execution.phase = "completed"
                else:
                    execution.phase = "admission"
            except BaseException as exception:
                original_exception = exception
                if exception is rejection_error:
                    status = "rejected"
                elif exception is abort_error:
                    status = "aborted"
                elif isinstance(exception, Exception):
                    status = "failed"
                else:
                    status = "interrupted"
                recoverable_admission = execution.phase == "admission" and exception is recoverable_admission_error
                if (
                    exception is not rejection_error
                    and not recoverable_admission
                    and (not isinstance(exception, (OperationError, ModelRetry, ApprovalRequired, CallDeferred)) or execution.phase == "inspection")
                ):
                    step_context.operation_failure = exception
                    step_context.operation_failure_stage = "operation_inspection" if execution.phase == "inspection" else "operation_execution"
            finished = OperationFinished(
                request=request,
                status=status,
                phase=execution.phase,
                evaluation_started=execution.evaluation_started,
                value=outcome,
                original_exception=original_exception,
                rejection=rejection_error.rejection if rejection_error is not None else None,
                blocked_by_operation_id=blocked_by.request.operation_id if blocked_by is not None else None,
            )
            lifecycle = _lifecycle_var.get()
            if lifecycle is not None and lifecycle.on_operation_finished is not None:
                try:
                    await await_notification(
                        lifecycle.on_operation_finished(finished),
                        interrupted=original_exception is not None and not isinstance(original_exception, Exception),
                        timeout=lifecycle.interruption_delivery_timeout,
                    )
                except BaseException as delivery_cause:
                    if original_exception is not None and not isinstance(original_exception, Exception):
                        chain_cleanup_errors(original_exception, delivery_cause)
                        raise original_exception from original_exception.__cause__
                    if not isinstance(delivery_cause, Exception):
                        step_context.operation_failure = delivery_cause
                        step_context.operation_failure_stage = "operation_delivery"
                        raise
                    error = OperationDeliveryError(finished, delivery_cause)
                    step_context.operation_failure = error
                    step_context.operation_failure_stage = "operation_delivery"
                    raise error from (original_exception if original_exception is not None else delivery_cause)
            if original_exception is not None:
                raise original_exception
            return outcome
        finally:
            _active_operation_context.reset(token)


def new_operation_request(
    step_context: StepContext,
    *,
    kind: OperationKind,
    expression: str | None = None,
    target_path: str | None = None,
    tool_name: str | None = None,
    expected_type: object = None,
    argument_name_to_value: Mapping[str, object] | None = None,
) -> OperationRequest:
    return OperationRequest(
        execution_reference=step_context.execution_reference,
        operation_id=generate_ulid(),
        kind=kind,
        expression=expression,
        target_path=target_path,
        tool_name=tool_name,
        expected_type=expected_type,
        argument_name_to_value=argument_name_to_value or {},
    )
