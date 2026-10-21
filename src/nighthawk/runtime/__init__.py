"""Runtime contracts and operation entry points, loaded without import cycles."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
    from .inspection import inspect_value
    from .operation_contract import (
        OperationDeliveryError,
        OperationError,
        OperationFinished,
        OperationRejectedError,
        OperationRejection,
        OperationRequest,
    )
    from .step_definition import BlockKind, StepDefinition
    from .step_executor import AsyncStepExecutor, ConfigurableStepExecutor, StepExecutor, SyncStepExecutor
    from .step_outcome import Break, Continue, OutcomeKind, Pass, Raise, Return, StepOutcome
    from .step_request import StepCandidate, StepRequest

_name_to_module = {
    "OutcomeKind": "step_outcome",
    "AsyncStepExecutor": "step_executor",
    "ConfigurableStepExecutor": "step_executor",
    "StepExecutor": "step_executor",
    "SyncStepExecutor": "step_executor",
    "StepOutcome": "step_outcome",
    "StepDefinition": "step_definition",
    "BlockKind": "step_definition",
    "BindingDeclarationError": "bindings",
    "BindingError": "bindings",
    "MISSING_ANNOTATION": "bindings",
    "BindingValidationError": "bindings",
    "infer_binding_types": "bindings",
    "validate_output_bindings": "bindings",
    "BindingWriter": "embedded",
    "execute_step": "embedded",
    "execute_step_async": "embedded",
    "ContextBuilder": "context",
    "ContextRequest": "context",
    "DefaultContextBuilder": "context",
    "StepRequest": "step_request",
    "StepCandidate": "step_request",
    "AssignmentResult": "assignment",
    "assign_expression": "assignment",
    "assign_expression_async": "assignment",
    "evaluate_expression": "expression",
    "evaluate_expression_async": "expression",
    "StepEnvironment": "environment",
    "inspect_value": "inspection",
    "OperationError": "operation_contract",
    "OperationDeliveryError": "operation_contract",
    "OperationRejectedError": "operation_contract",
    "OperationRejection": "operation_contract",
    "OperationFinished": "operation_contract",
    "OperationRequest": "operation_contract",
    "Pass": "step_outcome",
    "Return": "step_outcome",
    "Break": "step_outcome",
    "Continue": "step_outcome",
    "Raise": "step_outcome",
}

__all__ = [
    "OutcomeKind",
    "AsyncStepExecutor",
    "ConfigurableStepExecutor",
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
    "inspect_value",
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


def __getattr__(name: str) -> Any:
    if name not in _name_to_module:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{_name_to_module[name]}", __name__), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
