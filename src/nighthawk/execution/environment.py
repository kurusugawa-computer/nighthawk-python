from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from pydantic import BaseModel

from ..configuration import NaturalExecutionConfiguration
from ..errors import NighthawkError

if TYPE_CHECKING:
    from .executors import NaturalExecutor


@dataclass(frozen=True)
class NaturalExecutionEnvironment:
    natural_execution_configuration: NaturalExecutionConfiguration
    natural_executor: NaturalExecutor
    memory: BaseModel
    workspace_root: Path


_environment_var: ContextVar[NaturalExecutionEnvironment | None] = ContextVar(
    "nighthawk_environment",
    default=None,
)


def get_environment() -> NaturalExecutionEnvironment:
    environment_value = _environment_var.get()
    if environment_value is None:
        raise NighthawkError("Environment is not set")
    return environment_value


@contextmanager
def environment(environment_value: NaturalExecutionEnvironment) -> Iterator[None]:
    resolved = replace(
        environment_value,
        workspace_root=Path(environment_value.workspace_root).expanduser().resolve(),
    )

    from ..tools.registry import environment_scope

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
    natural_execution_configuration: NaturalExecutionConfiguration | None = None,
    natural_executor: NaturalExecutor | None = None,
    memory: BaseModel | None = None,
) -> Iterator[NaturalExecutionEnvironment]:
    current = get_environment()

    next_environment = current

    if natural_execution_configuration is not None:
        next_environment = replace(next_environment, natural_execution_configuration=natural_execution_configuration)

    if workspace_root is not None:
        resolved_root = Path(workspace_root).expanduser().resolve()  # type: ignore[arg-type]
        next_environment = replace(next_environment, workspace_root=resolved_root)

    if natural_executor is not None:
        next_environment = replace(next_environment, natural_executor=natural_executor)

    if memory is not None:
        next_environment = replace(next_environment, memory=memory)  # type: ignore[arg-type]

    from ..tools.registry import environment_scope

    with environment_scope():
        token = _environment_var.set(next_environment)
        try:
            yield next_environment
        finally:
            _environment_var.reset(token)
