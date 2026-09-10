"""Expression admission and live evaluation shared by every operation purpose."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Mapping
from types import CodeType
from typing import Literal

from ..errors import NighthawkError
from .async_bridge import run_coroutine_synchronously
from .environment import MissingEnvironmentNameError, UnboundEnvironmentNameError
from .operation_contract import OperationError
from .operations import OperationExecution, execute_operation_async, new_operation_request
from .step_context import StepContext


def compile_expression(expression: str) -> CodeType:
    try:
        tree = ast.parse(expression, mode="eval")
        if any(isinstance(node, ast.NamedExpr) for node in ast.walk(tree)):
            raise SyntaxError("Named assignments require nh_assign(target_path, expression)")
        return compile(tree, "<nighthawk-expression>", "eval", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except (SyntaxError, ValueError) as exception:
        raise OperationError(
            message=str(exception),
            phase="admission",
            evaluation_started=False,
            guidance="Evaluation has not started. Correct the expression syntax; use nh_assign for name assignments.",
        ) from exception


async def evaluate_compiled_expression(
    step_context: StepContext,
    compiled: CodeType,
    *,
    allow_awaitable_result: bool = True,
    override_name_to_value: Mapping[str, object] | None = None,
) -> object:
    namespace = step_context.environment.expression_namespace(override_name_to_value)
    try:
        value = eval(compiled, namespace, namespace)
        if inspect.isawaitable(value):
            if not allow_awaitable_result:
                if inspect.iscoroutine(value):
                    value.close()
                raise NighthawkError("Sync Natural function cannot return an awaitable value. Use async def and await the function call.")
            return await value
        return value
    except (MissingEnvironmentNameError, UnboundEnvironmentNameError) as exception:
        raise OperationError(
            message=str(exception),
            phase="evaluation",
            evaluation_started=True,
            guidance="Inspect the environment and correct the missing or unbound name. Earlier expression effects may remain; do not replay blindly.",
        ) from exception


async def evaluate_expression_operation(
    step_context: StepContext,
    expression: str,
    *,
    purpose: Literal["evaluation", "return"] = "evaluation",
    allow_awaitable_result: bool = True,
    expected_type: object = None,
    override_name_to_value: Mapping[str, object] | None = None,
) -> object:
    request = new_operation_request(step_context, kind=purpose, expression=expression, expected_type=expected_type)
    compiled: CodeType | None = None

    def admit() -> None:
        nonlocal compiled
        compiled = compile_expression(expression)

    async def execute(execution: OperationExecution) -> object:
        assert compiled is not None
        return await evaluate_compiled_expression(
            step_context, compiled, allow_awaitable_result=allow_awaitable_result, override_name_to_value=override_name_to_value
        )

    return await execute_operation_async(step_context, request, admit=admit, execute=execute)


async def eval_expression_async(step_context: StepContext, expression: str) -> object:
    return await evaluate_expression_operation(step_context, expression)


def eval_expression(step_context: StepContext, expression: str) -> object:
    return run_coroutine_synchronously(lambda: eval_expression_async(step_context, expression))
