"""A response-local rejection barrier, independent of model retry budgets."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .operation_contract import OperationRejection
from .step_context import StepContext


@dataclass
class ModelResponseExecution:
    step_context: StepContext
    rejection: OperationRejection | None = None


_model_response_execution: ContextVar[ModelResponseExecution | None] = ContextVar("nighthawk_model_response_execution", default=None)


@contextmanager
def model_response_scope(step_context: StepContext, *, reuse: bool = False) -> Iterator[ModelResponseExecution]:
    current = _model_response_execution.get()
    if reuse and current is not None and current.step_context is step_context:
        yield current
        return
    execution = ModelResponseExecution(step_context)
    token = _model_response_execution.set(execution)
    try:
        yield execution
    finally:
        _model_response_execution.reset(token)


def get_response_rejection(step_context: StepContext) -> OperationRejection | None:
    execution = _model_response_execution.get()
    return execution.rejection if execution is not None and execution.step_context is step_context else None


def record_response_rejection(step_context: StepContext, rejection: OperationRejection) -> None:
    execution = _model_response_execution.get()
    if execution is not None and execution.step_context is step_context:
        execution.rejection = rejection
