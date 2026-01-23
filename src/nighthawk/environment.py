from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel

from .agent import NaturalAgent
from .configuration import Configuration
from .errors import NighthawkError

NaturalBackend = Literal["stub", "agent"]


@dataclass(frozen=True)
class Environment:
    configuration: Configuration
    agent: NaturalAgent
    memory: BaseModel
    workspace_root: Path
    natural_backend: NaturalBackend = "agent"


_environment_var: ContextVar[Environment | None] = ContextVar(
    "nighthawk_environment",
    default=None,
)


def get_environment() -> Environment:
    environment_value = _environment_var.get()
    if environment_value is None:
        raise NighthawkError("Environment is not set")
    return environment_value


@contextmanager
def environment(environment_value: Environment) -> Iterator[None]:
    if environment_value.agent is None:
        raise NighthawkError("Environment agent is not set")
    if environment_value.memory is None:
        raise NighthawkError("Environment memory is not set")

    resolved = environment_value
    resolved = replace(
        resolved,
        workspace_root=Path(resolved.workspace_root).expanduser().resolve(),
    )

    from .tools import environment_scope

    with environment_scope():
        token = _environment_var.set(resolved)
        try:
            yield
        finally:
            _environment_var.reset(token)


@contextmanager
def environment_override(
    *,
    workspace_root: str | Path | None = None,
    configuration: Configuration | None = None,
    agent: NaturalAgent | None = None,
    memory: BaseModel | None = None,
    natural_backend: NaturalBackend | None = None,
) -> Iterator[Environment]:
    current = get_environment()

    next_environment = current

    if configuration is not None:
        next_environment = replace(next_environment, configuration=configuration)  # type: ignore[arg-type]

    if workspace_root is not None:
        resolved_root = Path(workspace_root).expanduser().resolve()  # type: ignore[arg-type]
        next_environment = replace(next_environment, workspace_root=resolved_root)

    if agent is not None:
        next_environment = replace(next_environment, agent=agent)  # type: ignore[arg-type]

    if memory is not None:
        next_environment = replace(next_environment, memory=memory)  # type: ignore[arg-type]

    if natural_backend is not None:
        next_environment = replace(next_environment, natural_backend=natural_backend)

    from .tools import environment_scope

    with environment_scope():
        token = _environment_var.set(next_environment)
        try:
            yield next_environment
        finally:
            _environment_var.reset(token)
