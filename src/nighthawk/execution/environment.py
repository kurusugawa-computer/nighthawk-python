from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from ..configuration import ExecutionConfiguration
from ..errors import NighthawkError

if TYPE_CHECKING:
    from .executors import ExecutionExecutor


@dataclass(frozen=True)
class ExecutionEnvironment:
    execution_configuration: ExecutionConfiguration
    execution_executor: ExecutionExecutor
    workspace_root: Path

    execution_system_prompt_suffix_fragments: tuple[str, ...] = ()
    execution_user_prompt_suffix_fragments: tuple[str, ...] = ()


_environment_var: ContextVar[ExecutionEnvironment | None] = ContextVar(
    "nighthawk_environment",
    default=None,
)


def get_environment() -> ExecutionEnvironment:
    environment_value = _environment_var.get()
    if environment_value is None:
        raise NighthawkError("Environment is not set")
    return environment_value


@contextmanager
def environment(environment_value: ExecutionEnvironment) -> Iterator[None]:
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
    execution_configuration: ExecutionConfiguration | None = None,
    execution_executor: ExecutionExecutor | None = None,
    execution_system_prompt_suffix_fragment: str | None = None,
    execution_user_prompt_suffix_fragment: str | None = None,
) -> Iterator[ExecutionEnvironment]:
    current = get_environment()

    next_environment = current

    if execution_configuration is not None:
        next_environment = replace(next_environment, execution_configuration=execution_configuration)

    if workspace_root is not None:
        resolved_root = Path(workspace_root).expanduser().resolve()  # type: ignore[arg-type]
        next_environment = replace(next_environment, workspace_root=resolved_root)

    if execution_executor is not None:
        next_environment = replace(next_environment, execution_executor=execution_executor)

    if execution_system_prompt_suffix_fragment is not None:
        next_environment = replace(
            next_environment,
            execution_system_prompt_suffix_fragments=(
                *next_environment.execution_system_prompt_suffix_fragments,
                execution_system_prompt_suffix_fragment,
            ),
        )

    if execution_user_prompt_suffix_fragment is not None:
        next_environment = replace(
            next_environment,
            execution_user_prompt_suffix_fragments=(
                *next_environment.execution_user_prompt_suffix_fragments,
                execution_user_prompt_suffix_fragment,
            ),
        )

    from ..tools.registry import environment_scope

    with environment_scope():
        token = _environment_var.set(next_environment)
        try:
            yield next_environment
        finally:
            _environment_var.reset(token)
