"""Executor-boundary contracts, runtime operations, and embedded execution."""

from .assignment import AssignmentResult, assign_expression, assign_expression_async
from .bindings import (
    MISSING_ANNOTATION,
    BindingDeclarationError,
    BindingError,
    BindingValidationError,
    infer_binding_types,
    validate_output_bindings,
)
from .context import ContextBuilder, ContextRequest, DefaultContextBuilder
from .embedded import BindingWriter, execute_step, execute_step_async
from .environment import StepEnvironment
from .expression import evaluate_expression, evaluate_expression_async
from .inspection import describe_value
from .operation_contract import (
    OperationDeliveryError,
    OperationError,
    OperationFinished,
    OperationRejectedError,
    OperationRejection,
    OperationRequest,
)
from .step_definition import BlockKind, StepDefinition
from .step_executor import AsyncStepExecutor, ConfigurableStepExecutor, ModelSelection, StepExecutor, SyncStepExecutor
from .step_outcome import Break, Continue, OutcomeKind, Pass, Raise, Return, StepOutcome
from .step_request import StepCandidate, StepRequest

__all__ = [
    "OutcomeKind",
    "AsyncStepExecutor",
    "ConfigurableStepExecutor",
    "ModelSelection",
    "StepExecutor",
    "SyncStepExecutor",
    "StepOutcome",
    "StepDefinition",
    "BlockKind",
    "BindingDeclarationError",
    "BindingError",
    "MISSING_ANNOTATION",
    "BindingValidationError",
    "infer_binding_types",
    "validate_output_bindings",
    "BindingWriter",
    "execute_step",
    "execute_step_async",
    "ContextBuilder",
    "ContextRequest",
    "DefaultContextBuilder",
    "StepCandidate",
    "StepRequest",
    "AssignmentResult",
    "assign_expression",
    "assign_expression_async",
    "evaluate_expression",
    "evaluate_expression_async",
    "StepEnvironment",
    "describe_value",
    "OperationError",
    "OperationDeliveryError",
    "OperationRejectedError",
    "OperationRejection",
    "OperationFinished",
    "OperationRequest",
    "Pass",
    "Return",
    "Break",
    "Continue",
    "Raise",
]
