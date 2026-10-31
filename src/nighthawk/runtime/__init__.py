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
from .model_outcome import ModelOutcome, ModelStepResponse
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
from .step_definition import BlockKind, StepDefinition
from .step_executor import (
    AsyncExecutionAgent,
    AsyncStepExecutor,
    ConfigurableStepExecutor,
    ModelSelection,
    StepExecutionAgent,
    StepExecutor,
    SyncExecutionAgent,
    SyncStepExecutor,
)
from .step_outcome import Break, Continue, OutcomeKind, Pass, Raise, Return, StepOutcome
from .step_request import StepCandidate, StepRequest

__all__ = [
    "assign_expression",
    "assign_expression_async",
    "AssignmentResult",
    "AsyncExecutionAgent",
    "AsyncStepExecutor",
    "BindingDeclarationError",
    "BindingError",
    "BindingValidationError",
    "BindingWriter",
    "BlockKind",
    "Break",
    "ConfigurableStepExecutor",
    "ContextBuilder",
    "ContextRequest",
    "Continue",
    "DefaultContextBuilder",
    "describe_value",
    "evaluate_expression",
    "evaluate_expression_async",
    "execute_step",
    "execute_step_async",
    "infer_binding_types",
    "MISSING_ANNOTATION",
    "ModelOutcome",
    "ModelSelection",
    "ModelStepResponse",
    "OperationDeliveryError",
    "OperationError",
    "OperationFinished",
    "OperationKind",
    "OperationPhase",
    "OperationRejectedError",
    "OperationRejection",
    "OperationRequest",
    "OperationStatus",
    "OutcomeKind",
    "Pass",
    "Raise",
    "Return",
    "StepCandidate",
    "StepDefinition",
    "StepEnvironment",
    "StepExecutionAgent",
    "StepExecutor",
    "StepOutcome",
    "StepRequest",
    "SyncExecutionAgent",
    "SyncStepExecutor",
    "validate_output_bindings",
]
