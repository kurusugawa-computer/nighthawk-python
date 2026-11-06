"""Expression admission and live evaluation shared by every operation purpose."""

from __future__ import annotations

import ast
import inspect
from types import CodeType
from typing import Literal

from nighthawk.core.async_bridge import run_coroutine_synchronously
from nighthawk.core.environment import MissingEnvironmentNameError, UnboundEnvironmentNameError
from nighthawk.core.operations import OperationExecution, execute_operation_async, new_operation_request
from nighthawk.core.step_context import StepContext
from nighthawk.errors import NighthawkError


def compile_expression(expression: str, execution: OperationExecution) -> CodeType:
    try:
        tree = ast.parse(expression, mode="eval")
        if any(isinstance(node, ast.NamedExpr) for node in ast.walk(tree)):
            raise SyntaxError("Named assignments require nh_assign(target_path, expression)")
        return compile(tree, "<nighthawk-expression>", "eval", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except (SyntaxError, ValueError) as exception:
        raise execution.diagnose(
            str(exception),
            guidance="Evaluation has not started. Correct the expression syntax; use nh_assign for name assignments.",
        ) from exception


async def evaluate_compiled_expression(
    step_context: StepContext,
    compiled: CodeType,
    execution: OperationExecution,
    *,
    allow_awaitable_result: bool = True,
) -> object:
    namespace = step_context.environment._expression_namespace()
    try:
        value = eval(compiled, namespace, namespace)
        if inspect.isawaitable(value):
            if not allow_awaitable_result:
                # eval creates the top-level-await wrapper itself. Other returned
                # awaitables may be borrowed from application state, including a
                # Python helper that retains its result; Core cannot dispose them.
                if compiled.co_flags & inspect.CO_COROUTINE and inspect.iscoroutine(value):
                    value.close()
                raise NighthawkError("Sync Natural function cannot return an awaitable value. Use async def and await the function call.")
            return await value
        return value
    except (MissingEnvironmentNameError, UnboundEnvironmentNameError) as exception:
        raise execution.diagnose(
            str(exception),
            guidance="Inspect the environment and correct the missing or unbound name. Earlier expression effects may remain; do not replay blindly.",
        ) from exception


async def evaluate_expression_operation(
    step_context: StepContext,
    expression: str,
    *,
    purpose: Literal["evaluation", "return"] = "evaluation",
    allow_awaitable_result: bool = True,
    expected_type: object = None,
) -> object:
    request = new_operation_request(step_context, kind=purpose, expression=expression, expected_type=expected_type)
    compiled: CodeType | None = None

    def admit(execution: OperationExecution) -> None:
        nonlocal compiled
        compiled = compile_expression(expression, execution)

    async def execute(execution: OperationExecution) -> object:
        assert compiled is not None
        return await evaluate_compiled_expression(step_context, compiled, execution, allow_awaitable_result=allow_awaitable_result)

    return await execute_operation_async(step_context, request, admit=admit, execute=execute)


async def evaluate_expression_async(step_context: StepContext, expression: str) -> object:
    """Evaluate one Python expression through operation oversight and lifecycle delivery.

    Names resolve in the context's live environment; the returned Python value retains identity.
    An awaitable result is awaited once, including when an ordinary helper returns it.
    Named assignments require assign_expression_async so working writes remain tracked.
    Syntax and runtime name-resolution errors carry recoverable operation diagnostics; other evaluation exceptions propagate.
    Effects from calls and in-place mutations may remain after failure or rejection.
    """
    return await evaluate_expression_operation(step_context, expression)


def evaluate_expression(step_context: StepContext, expression: str) -> object:
    """Synchronous counterpart of evaluate_expression_async, using the same operation boundary."""
    return run_coroutine_synchronously(lambda: evaluate_expression_async(step_context, expression))
