from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from types import CellType
from typing import Iterator

from ..configuration import JsonRendererStyle
from ..errors import NighthawkError


@dataclass(frozen=True)
class ToolResultRenderingPolicy:
    tokenizer_encoding_name: str
    tool_result_max_tokens: int
    json_renderer_style: JsonRendererStyle


@dataclass
class StepContext:
    step_id: str

    step_globals: dict[str, object]
    step_locals: dict[str, object]

    binding_commit_targets: set[str]

    # Ordinary user-provided binding (for example a global named "memory") may exist in step_locals.

    binding_name_to_type: dict[str, object] = field(default_factory=dict)
    assigned_binding_names: set[str] = field(default_factory=set)
    step_locals_revision: int = 0
    tool_result_rendering_policy: ToolResultRenderingPolicy | None = None


_step_execution_stack_var: ContextVar[tuple[StepContext, ...]] = ContextVar(
    "nighthawk_step_context_stack",
    default=(),
)


@dataclass(frozen=True)
class PythonLookupState:
    python_name_scope_stack: tuple[dict[str, object], ...] = ()
    python_cell_scope_stack: tuple[dict[str, CellType], ...] = ()


_python_lookup_state_var: ContextVar[PythonLookupState] = ContextVar(
    "nighthawk_python_lookup_state",
    default=PythonLookupState(),
)


@contextmanager
def step_context_scope(step_context: StepContext) -> Iterator[None]:
    current_stack = _step_execution_stack_var.get()
    token = _step_execution_stack_var.set((*current_stack, step_context))
    try:
        yield
    finally:
        _step_execution_stack_var.reset(token)


@contextmanager
def python_name_scope(name_to_value: dict[str, object]) -> Iterator[None]:
    current_lookup_state = _python_lookup_state_var.get()
    next_lookup_state = replace(
        current_lookup_state,
        python_name_scope_stack=(*current_lookup_state.python_name_scope_stack, dict(name_to_value)),
    )
    token = _python_lookup_state_var.set(next_lookup_state)
    try:
        yield
    finally:
        _python_lookup_state_var.reset(token)


@contextmanager
def python_cell_scope(name_to_cell: dict[str, CellType]) -> Iterator[None]:
    current_lookup_state = _python_lookup_state_var.get()
    next_lookup_state = replace(
        current_lookup_state,
        python_cell_scope_stack=(*current_lookup_state.python_cell_scope_stack, dict(name_to_cell)),
    )
    token = _python_lookup_state_var.set(next_lookup_state)
    try:
        yield
    finally:
        _python_lookup_state_var.reset(token)


def get_step_context_stack() -> tuple[StepContext, ...]:
    return _step_execution_stack_var.get()


def get_python_name_scope_stack() -> tuple[dict[str, object], ...]:
    return _python_lookup_state_var.get().python_name_scope_stack


def get_python_cell_scope_stack() -> tuple[dict[str, CellType], ...]:
    return _python_lookup_state_var.get().python_cell_scope_stack


def get_current_step_context() -> StepContext:
    stack = _step_execution_stack_var.get()
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
