from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import CellType
from typing import TYPE_CHECKING

from nighthawk.core.environment import StepEnvironment
from nighthawk.core.execution_reference import ExecutionReference
from nighthawk.errors import NighthawkError

if TYPE_CHECKING:
    from nighthawk.core.lifecycle import FailureStage
    from nighthawk.core.operation_contract import OperationFinished
    from nighthawk.core.oversight import StepAbortedError


@dataclass
class _OperationState:
    """Runtime-owned serialization, terminal failure, and recovered failure facts for one step's operations."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    failure: BaseException | None = None
    failure_stage: FailureStage | None = None
    abort: StepAbortedError | None = None
    failed_operation_list: list[OperationFinished] = field(default_factory=list)


@dataclass
class StepContext:
    """Mutable, per-step execution context passed to tools and executors.

    ``environment`` owns effective lookup, declared outputs and their contracts, and tracked step assignments.
    ``step_locals`` holds the Python working values captured at block entry.
    Finalization validates initialized declared outputs when the candidate omits its output selection; an explicit mapping supplies the complete selection.
    """

    execution_reference: ExecutionReference
    environment: StepEnvironment
    step_locals: dict[str, object]
    read_binding_names: frozenset[str] = frozenset()
    type_alias_reference_names: frozenset[str] = frozenset()
    executor_result: object = None
    _operation_state: _OperationState = field(default_factory=_OperationState, init=False, repr=False)


_step_context_stack_var: ContextVar[tuple[StepContext, ...]] = ContextVar(
    "nighthawk_step_context_stack",
    default=(),
)


_python_cell_scope_stack: ContextVar[tuple[dict[str, CellType], ...]] = ContextVar("nighthawk_python_cell_scope_stack", default=())


@contextmanager
def step_context_scope(step_context: StepContext) -> Generator[None]:
    current_stack = _step_context_stack_var.get()
    token = _step_context_stack_var.set((*current_stack, step_context))
    try:
        yield
    finally:
        _step_context_stack_var.reset(token)


@contextmanager
def python_cell_scope(name_to_cell: dict[str, CellType]) -> Generator[None]:
    token = _python_cell_scope_stack.set((*_python_cell_scope_stack.get(), dict(name_to_cell)))
    try:
        yield
    finally:
        _python_cell_scope_stack.reset(token)


def get_python_cell_scope_stack() -> tuple[dict[str, CellType], ...]:
    return _python_cell_scope_stack.get()


def get_step_context() -> StepContext:
    """Return the innermost active step context.

    Raises:
        NighthawkError: If no step context is set (i.e. called outside step execution).
    """
    stack = _step_context_stack_var.get()
    if not stack:
        raise NighthawkError("StepContext is not set")
    return stack[-1]
