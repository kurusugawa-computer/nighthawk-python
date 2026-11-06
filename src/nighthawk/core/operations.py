"""One serialized admission, inspection, execution, and notification pipeline."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from nighthawk._identifiers import generate_ulid
from nighthawk.core.interruption import chain_cleanup_errors
from nighthawk.core.notifications import await_notification
from nighthawk.core.operation_contract import (
    OperationDeliveryError,
    OperationError,
    OperationFinished,
    OperationKind,
    OperationPhase,
    OperationRejectedError,
    OperationRejection,
    OperationRequest,
    OperationStatus,
    TerminalOperationError,
)
from nighthawk.core.oversight import AbortStep, Accept, RejectOperation, StepAbortedError, record_oversight_decision
from nighthawk.core.step_context import StepContext
from nighthawk.errors import NighthawkError

_active_operation_context: ContextVar[StepContext | None] = ContextVar("nighthawk_active_operation_context", default=None)


@dataclass
class OperationExecution:
    request: OperationRequest
    phase: OperationPhase = "admission"
    evaluation_started: bool = False
    diagnostic: OperationError | None = None

    def diagnose(self, message: str, *, guidance: str) -> OperationError:
        self.diagnostic = OperationError(message=message, phase=self.phase, evaluation_started=self.evaluation_started, guidance=guidance)
        return self.diagnostic


async def execute_operation_async(
    step_context: StepContext,
    request: OperationRequest,
    *,
    admit: Callable[[OperationExecution], None],
    execute: Callable[[OperationExecution], Awaitable[object]],
    blocked_by: OperationRejection | None = None,
) -> object:
    """Run one operation; an ordinary exception from admission or its body fails only that operation.

    The failed operation is retained with its original exception and the exception is re-raised to the caller.
    Inspection failures, aborts, interruptions, delivery failures, and TerminalOperationError end the step.
    """
    from nighthawk.core.scoping import optional_scope

    active_scope = optional_scope()
    if _active_operation_context.get() is step_context:
        raise NighthawkError("A runtime operation cannot await another operation on its own step; use ordinary Python composition")
    async with step_context._operation_state.lock:
        if step_context._operation_state.failure is not None:
            raise step_context._operation_state.failure
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
                    admit(execution)
                execution.phase = "inspection"
                oversight = active_scope.oversight if active_scope is not None else None
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
                        step_context._operation_state.abort = abort_error
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
                if exception is not rejection_error and (
                    execution.phase == "inspection" or not isinstance(exception, Exception) or isinstance(exception, TerminalOperationError)
                ):
                    step_context._operation_state.failure = exception
                    step_context._operation_state.failure_stage = "operation_inspection" if execution.phase == "inspection" else "operation_execution"
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
            if status == "failed" and step_context._operation_state.failure is not original_exception:
                step_context._operation_state.failed_operation_list.append(finished)
            lifecycle = active_scope.lifecycle if active_scope is not None else None
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
                        step_context._operation_state.failure = delivery_cause
                        step_context._operation_state.failure_stage = "operation_delivery"
                        raise
                    error = OperationDeliveryError(finished, delivery_cause)
                    step_context._operation_state.failure = error
                    step_context._operation_state.failure_stage = "operation_delivery"
                    raise error from (original_exception if original_exception is not None else delivery_cause)
            if original_exception is not None:
                raise original_exception
            return outcome
        finally:
            _active_operation_context.reset(token)


def failed_operation(
    step_context: StepContext, *, operation_id: str | None = None, exception: BaseException | None = None
) -> OperationFinished | None:
    """Find a recovered failed operation of this step by its Id or its original exception."""
    for finished in reversed(step_context._operation_state.failed_operation_list):
        if finished.request.operation_id == operation_id or (exception is not None and finished.original_exception is exception):
            return finished
    return None


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
