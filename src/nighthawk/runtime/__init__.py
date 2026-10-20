"""Runtime contracts and operation entry points, loaded without import cycles."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .assignment import AssignmentResult, assign_tool, assign_tool_async
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
    from .expression import eval_expression, eval_expression_async
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
    from .step_request import StepCandidate, StepRequest
    from .step_result import Break, Continue, Pass, Raise, Return, StepResult

_name_to_module = {
    "StepResult": "step_result",
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
    "assign_tool": "assignment",
    "assign_tool_async": "assignment",
    "eval_expression": "expression",
    "eval_expression_async": "expression",
    "StepEnvironment": "environment",
    "inspect_value": "inspection",
    "OperationError": "operation_contract",
    "OperationDeliveryError": "operation_contract",
    "OperationRejectedError": "operation_contract",
    "OperationRejection": "operation_contract",
    "OperationFinished": "operation_contract",
    "OperationRequest": "operation_contract",
    "Pass": "step_result",
    "Return": "step_result",
    "Break": "step_result",
    "Continue": "step_result",
    "Raise": "step_result",
}

__all__ = [
    "StepResult",
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
    "assign_tool",
    "assign_tool_async",
    "eval_expression",
    "eval_expression_async",
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
        raise AttributeError(name)
    return getattr(import_module(f".{_name_to_module[name]}", __name__), name)
