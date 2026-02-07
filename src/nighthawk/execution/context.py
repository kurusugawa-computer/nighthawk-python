from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import CellType
from typing import Iterator

from ..configuration import ExecutionConfiguration
from ..errors import NighthawkError


@dataclass
class ExecutionContext:
    execution_id: str
    execution_configuration: ExecutionConfiguration

    execution_globals: dict[str, object]
    execution_locals: dict[str, object]

    binding_commit_targets: set[str]

    # Ordinary user-provided binding (for example a global named "memory") may exist in execution_locals.

    binding_name_to_type: dict[str, object] = field(default_factory=dict)
    assigned_binding_names: set[str] = field(default_factory=set)
    execution_locals_revision: int = 0


_execution_context_stack_var: ContextVar[list[ExecutionContext]] = ContextVar(
    "nighthawk_execution_context_stack",
    default=[],
)

_python_name_scope_stack_var: ContextVar[list[dict[str, object]]] = ContextVar(
    "nighthawk_python_name_scope_stack",
    default=[],
)

_python_cell_scope_stack_var: ContextVar[list[dict[str, CellType]]] = ContextVar(
    "nighthawk_python_cell_scope_stack",
    default=[],
)


@contextmanager
def execution_context_scope(execution_context: ExecutionContext) -> Iterator[None]:
    current = _execution_context_stack_var.get()
    token = _execution_context_stack_var.set([*current, execution_context])
    try:
        yield
    finally:
        _execution_context_stack_var.reset(token)


@contextmanager
def python_name_scope(name_to_value: dict[str, object]) -> Iterator[None]:
    current = _python_name_scope_stack_var.get()
    token = _python_name_scope_stack_var.set([*current, dict(name_to_value)])
    try:
        yield
    finally:
        _python_name_scope_stack_var.reset(token)


@contextmanager
def python_cell_scope(name_to_cell: dict[str, CellType]) -> Iterator[None]:
    current = _python_cell_scope_stack_var.get()
    token = _python_cell_scope_stack_var.set([*current, dict(name_to_cell)])
    try:
        yield
    finally:
        _python_cell_scope_stack_var.reset(token)


def get_execution_context_stack() -> tuple[ExecutionContext, ...]:
    return tuple(_execution_context_stack_var.get())


def get_python_name_scope_stack() -> tuple[dict[str, object], ...]:
    return tuple(_python_name_scope_stack_var.get())


def get_python_cell_scope_stack() -> tuple[dict[str, CellType], ...]:
    return tuple(_python_cell_scope_stack_var.get())


def get_current_execution_context() -> ExecutionContext:
    stack = _execution_context_stack_var.get()
    if not stack:
        raise NighthawkError("ExecutionContext is not set")
    return stack[-1]
