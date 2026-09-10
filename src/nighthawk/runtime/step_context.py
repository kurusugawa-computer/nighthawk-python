from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from types import CellType
from typing import TYPE_CHECKING

from ..errors import NighthawkError
from ..json_renderer import JsonRendererStyle
from .environment import StepEnvironment
from .execution_reference import ExecutionReference

if TYPE_CHECKING:
    from ..lifecycle import FailureStage

_MISSING: object = object()
"""Sentinel indicating a name could not be resolved. Distinct from None."""


@dataclass(frozen=True)
class ToolResultRenderingPolicy:
    tokenizer_encoding_name: str
    tool_result_max_tokens: int
    json_renderer_style: JsonRendererStyle


DEFAULT_TOOL_RESULT_RENDERING_POLICY = ToolResultRenderingPolicy(
    tokenizer_encoding_name="o200k_base",
    tool_result_max_tokens=2_000,
    json_renderer_style="default",
)


def resolve_tool_result_rendering_policy(policy: ToolResultRenderingPolicy | None) -> ToolResultRenderingPolicy:
    """Return *policy* when set, else :data:`DEFAULT_TOOL_RESULT_RENDERING_POLICY`."""
    if policy is None:
        return DEFAULT_TOOL_RESULT_RENDERING_POLICY
    return policy


@dataclass
class StepContext:
    """Mutable, per-step execution context passed to tools and executors.

    ``step_globals`` and ``step_locals`` are mutable dicts. All mutations to ``step_locals`` MUST go through :meth:`record_assignment` (for top-level name bindings) or through the dotted-path assignment in ``tools.assignment`` (which bumps ``step_locals_revision`` directly).
    Direct dict writes bypass revision tracking, ``assigned_binding_names``, and ``dirty_output_binding_names`` bookkeeping, which will cause incorrect commit behavior at Natural block boundaries.
    """

    execution_reference: ExecutionReference

    step_globals: dict[str, object]
    step_locals: dict[str, object]

    binding_commit_targets: set[str]
    read_binding_names: frozenset[str]
    implicit_reference_name_to_value: dict[str, object]

    # Ordinary user-provided binding (for example a global named "memory") may exist in step_locals.

    processed_natural_program: str = ""
    binding_name_to_type: dict[str, object] = field(default_factory=dict)
    assigned_binding_names: set[str] = field(default_factory=set)
    dirty_output_binding_names: set[str] = field(default_factory=set)
    step_locals_revision: int = 0
    tool_result_rendering_policy: ToolResultRenderingPolicy | None = None
    environment: StepEnvironment = field(init=False)
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    operation_failure: BaseException | None = field(default=None, init=False, repr=False)
    operation_failure_stage: FailureStage | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.environment = StepEnvironment(
            python_name_to_value=self.step_locals,
            python_globals=self.step_globals,
            task_name_to_value=self.implicit_reference_name_to_value,
            output_name_set=frozenset(self.binding_commit_targets),
            binding_name_to_type=self.binding_name_to_type,
        )

    def record_assignment(self, name: str, value: object) -> None:
        """Record an assignment to a step local variable.

        Updates step_locals, marks the name as assigned, and bumps the revision.
        """
        self.environment.assign(name, value)
        self.step_locals[name] = value
        self.assigned_binding_names.add(name)
        self.step_locals_revision += 1

    def record_output_binding_mutation(self, name: str) -> None:
        """Record an in-place mutation affecting a committed output binding root."""
        self.dirty_output_binding_names.add(name)
        self.step_locals_revision += 1


_step_context_stack_var: ContextVar[tuple[StepContext, ...]] = ContextVar(
    "nighthawk_step_context_stack",
    default=(),
)


@dataclass(frozen=True)
class _PythonLookupState:
    python_cell_scope_stack: tuple[dict[str, CellType], ...] = ()


_python_lookup_state_var: ContextVar[_PythonLookupState] = ContextVar(
    "nighthawk_python_lookup_state",
    default=_PythonLookupState(),  # noqa: B039
)


@contextmanager
def step_context_scope(step_context: StepContext) -> Iterator[None]:
    current_stack = _step_context_stack_var.get()
    token = _step_context_stack_var.set((*current_stack, step_context))
    try:
        yield
    finally:
        _step_context_stack_var.reset(token)


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
    return _step_context_stack_var.get()


def get_python_cell_scope_stack() -> tuple[dict[str, CellType], ...]:
    return _python_lookup_state_var.get().python_cell_scope_stack


def get_step_context() -> StepContext:
    """Return the innermost active step context.

    Raises:
        NighthawkError: If no step context is set (i.e. called outside step execution).
    """
    stack = _step_context_stack_var.get()
    if not stack:
        raise NighthawkError("StepContext is not set")
    return stack[-1]


def resolve_name_in_step_context(step_context: StepContext, name: str) -> object:
    """Resolve a name from step locals, step globals, or builtins.

    Returns the resolved value, or ``_MISSING`` if the name cannot be found.
    Callers must compare the result with ``value is _MISSING`` to detect
    absent names (since the resolved value itself may be ``None``).
    """
    try:
        return step_context.environment.resolve(name)
    except UnboundLocalError:
        raise
    except NameError:
        return _MISSING
