from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import CellType
from typing import TYPE_CHECKING, Any

from pydantic_ai import AgentRunResult

from ..errors import NighthawkError
from ..json_renderer import JsonRendererStyle
from .environment import StepEnvironment
from .execution_reference import ExecutionReference

if TYPE_CHECKING:
    from ..lifecycle import FailureStage
    from ..oversight import StepAbortedError

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
    if policy is None:
        return DEFAULT_TOOL_RESULT_RENDERING_POLICY
    return policy


@dataclass
class _OperationState:
    """Runtime-owned serialization and terminal failure state for one step's operations."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    failure: BaseException | None = None
    failure_stage: FailureStage | None = None
    abort: StepAbortedError | None = None


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
    model_result: AgentRunResult[Any] | None = None
    tool_result_rendering_policy: ToolResultRenderingPolicy | None = None
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


def resolve_name_in_step_context(step_context: StepContext, name: str) -> object:
    try:
        return step_context.environment.resolve(name)
    except UnboundLocalError:
        raise
    except NameError:
        return _MISSING
