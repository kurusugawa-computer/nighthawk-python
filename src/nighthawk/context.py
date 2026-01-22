from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel
from pydantic_ai import Agent

from .configuration import Configuration
from .errors import NighthawkError


@dataclass(frozen=True)
class RuntimeContext:
    configuration: Configuration
    workspace_root: Path | None = None
    agent: Agent | None = None
    memory: BaseModel | None = None


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
def runtime_context(ctx: RuntimeContext) -> Iterator[None]:
    resolved = ctx
    if resolved.workspace_root is not None:
        resolved = replace(
            resolved,
            workspace_root=Path(resolved.workspace_root).expanduser().resolve(),
        )

    if resolved.memory is None:
        resolved = replace(resolved, memory=resolved.configuration.create_memory())

    token = _runtime_context_var.set(resolved)
    try:
        yield
    finally:
        _runtime_context_var.reset(token)


_UNSET: object = object()


@contextmanager
def runtime_context_override(
    *,
    workspace_root: str | Path | None | object = _UNSET,
    configuration: Configuration | object = _UNSET,
    agent: Agent | None | object = _UNSET,
    memory: BaseModel | None | object = _UNSET,
) -> Iterator[None]:
    current = get_runtime_context()

    next_ctx = current

    if configuration is not _UNSET:
        next_ctx = replace(next_ctx, configuration=configuration)  # type: ignore[arg-type]
        if memory is _UNSET:
            next_ctx = replace(next_ctx, memory=next_ctx.configuration.create_memory())

    if workspace_root is not _UNSET:
        resolved_root = None
        if workspace_root is not None:
            resolved_root = Path(workspace_root).expanduser().resolve()  # type: ignore[arg-type]
        next_ctx = replace(next_ctx, workspace_root=resolved_root)

    if agent is not _UNSET:
        next_ctx = replace(next_ctx, agent=agent)  # type: ignore[arg-type]

    if memory is not _UNSET:
        next_ctx = replace(next_ctx, memory=memory)  # type: ignore[arg-type]

    token = _runtime_context_var.set(next_ctx)
    try:
        yield
    finally:
        _runtime_context_var.reset(token)
