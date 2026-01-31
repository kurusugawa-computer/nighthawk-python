from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from pydantic import BaseModel

from ..configuration import ExecutionConfiguration
from ..errors import NighthawkError


@dataclass
class ExecutionContext:
    execution_id: str
    execution_configuration: ExecutionConfiguration

    execution_globals: dict[str, object]
    execution_locals: dict[str, object]

    binding_commit_targets: set[str]

    memory: BaseModel | None

    binding_name_to_type: dict[str, object] = field(default_factory=dict)
    assigned_binding_names: set[str] = field(default_factory=set)
    execution_locals_revision: int = 0


_execution_context_stack_var: ContextVar[list[ExecutionContext]] = ContextVar(
    "nighthawk_execution_context_stack",
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


def get_execution_context_stack() -> tuple[ExecutionContext, ...]:
    return tuple(_execution_context_stack_var.get())


def get_current_execution_context() -> ExecutionContext:
    stack = _execution_context_stack_var.get()
    if not stack:
        raise NighthawkError("ExecutionContext is not set")
    return stack[-1]
