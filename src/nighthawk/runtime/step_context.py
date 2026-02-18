from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import CellType
from typing import Iterator

from ..configuration import RunConfiguration
from ..errors import NighthawkError


@dataclass
class StepContext:
    step_id: str
    run_configuration: RunConfiguration

    step_globals: dict[str, object]
    step_locals: dict[str, object]

    binding_commit_targets: set[str]

    # Ordinary user-provided binding (for example a global named "memory") may exist in step_locals.

    binding_name_to_type: dict[str, object] = field(default_factory=dict)
    assigned_binding_names: set[str] = field(default_factory=set)
    step_locals_revision: int = 0


_step_context_stack_var: ContextVar[list[StepContext]] = ContextVar(
    "nighthawk_step_context_stack",
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
def step_context_scope(step_context: StepContext) -> Iterator[None]:
    current = _step_context_stack_var.get()
    token = _step_context_stack_var.set([*current, step_context])
    try:
        yield
    finally:
        _step_context_stack_var.reset(token)


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


def get_step_context_stack() -> tuple[StepContext, ...]:
    return tuple(_step_context_stack_var.get())


def get_python_name_scope_stack() -> tuple[dict[str, object], ...]:
    return tuple(_python_name_scope_stack_var.get())


def get_python_cell_scope_stack() -> tuple[dict[str, CellType], ...]:
    return tuple(_python_cell_scope_stack_var.get())


def get_current_step_context() -> StepContext:
    stack = _step_context_stack_var.get()
    if not stack:
        raise NighthawkError("StepContext is not set")
    return stack[-1]


def resolve_name_in_step_context(step_context: StepContext, name: str) -> object | None:
    if name in step_context.step_locals:
        return step_context.step_locals[name]

    if name in step_context.step_globals:
        return step_context.step_globals[name]

    python_builtins = step_context.step_globals.get("__builtins__", __builtins__)

    if isinstance(python_builtins, dict):
        return python_builtins.get(name)

    if hasattr(python_builtins, name):
        return getattr(python_builtins, name)

    return None
