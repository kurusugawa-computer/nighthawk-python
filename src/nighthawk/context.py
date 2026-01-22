from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel
from pydantic_ai import Agent

from .configuration import Configuration
from .errors import NighthawkError

NaturalBackend = Literal["stub", "agent"]


@dataclass(frozen=True)
class RuntimeContext:
    configuration: Configuration
    agent: Agent
    memory: BaseModel
    workspace_root: Path
    natural_backend: NaturalBackend = "agent"


_runtime_context_var: ContextVar[RuntimeContext | None] = ContextVar(
    "nighthawk_runtime_context",
    default=None,
)


def get_runtime_context() -> RuntimeContext:
    ctx = _runtime_context_var.get()
    if ctx is None:
        raise NighthawkError("Runtime context is not set")
    return ctx


@contextmanager
def runtime_context(context: RuntimeContext) -> Iterator[None]:
    if context.agent is None:
        raise NighthawkError("Runtime context agent is not set")
    if context.memory is None:
        raise NighthawkError("Runtime context memory is not set")

    resolved = context
    if resolved.workspace_root is not None:
        resolved = replace(
            resolved,
            workspace_root=Path(resolved.workspace_root).expanduser().resolve(),
        )

    token = _runtime_context_var.set(resolved)
    try:
        yield
    finally:
        _runtime_context_var.reset(token)


@contextmanager
def runtime_context_override(
    *,
    workspace_root: str | Path | None = None,
    configuration: Configuration | None = None,
    agent: Agent | None = None,
    memory: BaseModel | None = None,
    natural_backend: NaturalBackend | None = None,
) -> Iterator[RuntimeContext]:
    current = get_runtime_context()

    next_context = current

    if configuration is not None:
        next_context = replace(next_context, configuration=configuration)  # type: ignore[arg-type]

    if workspace_root is not None:
        resolved_root = Path(workspace_root).expanduser().resolve()  # type: ignore[arg-type]
        next_context = replace(next_context, workspace_root=resolved_root)

    if agent is not None:
        next_context = replace(next_context, agent=agent)  # type: ignore[arg-type]

    if memory is not None:
        next_context = replace(next_context, memory=memory)  # type: ignore[arg-type]

    if natural_backend is not None:
        next_context = replace(next_context, natural_backend=natural_backend)

    token = _runtime_context_var.set(next_context)
    try:
        yield next_context
    finally:
        _runtime_context_var.reset(token)
