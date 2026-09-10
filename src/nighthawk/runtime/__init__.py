"""Runtime contracts and operation entry points, loaded without import cycles."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .assignment import AssignmentResult, assign_tool, assign_tool_async
    from .environment import StepEnvironment
    from .expression import eval_expression, eval_expression_async
    from .operation_contract import OperationError, OperationFinished, OperationRequest
    from .step_result import Break, Continue, Pass, Raise, Return

_name_to_module = {
    "AssignmentResult": "assignment",
    "assign_tool": "assignment",
    "assign_tool_async": "assignment",
    "eval_expression": "expression",
    "eval_expression_async": "expression",
    "StepEnvironment": "environment",
    "OperationError": "operation_contract",
    "OperationFinished": "operation_contract",
    "OperationRequest": "operation_contract",
    "Pass": "step_result",
    "Return": "step_result",
    "Break": "step_result",
    "Continue": "step_result",
    "Raise": "step_result",
}

__all__ = [
    "AssignmentResult", "assign_tool", "assign_tool_async", "eval_expression", "eval_expression_async",
    "StepEnvironment", "OperationError", "OperationFinished", "OperationRequest", "Pass", "Return", "Break", "Continue", "Raise",
]


def __getattr__(name: str) -> Any:
    if name not in _name_to_module:
        raise AttributeError(name)
    return getattr(import_module(f".{_name_to_module[name]}", __name__), name)
