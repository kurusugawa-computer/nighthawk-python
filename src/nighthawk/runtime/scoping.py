from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import logfire

from ..configuration import RunConfiguration
from ..errors import NighthawkError
from .environment import Environment

if TYPE_CHECKING:
    from .step_executor import StepExecutor


RUN_ID = "run.id"
SCOPE_ID = "scope.id"
STEP_ID = "step.id"
TOOL_CALL_ID = "tool_call.id"


@contextmanager
def span(span_name: str, /, **attributes: Any) -> Iterator[None]:
    with logfire.span(span_name, **attributes):
        yield


def _generate_id() -> str:
    return str(uuid.uuid4())


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
def run(environment_value: Environment) -> Iterator[None]:
    resolved = replace(
        environment_value,
        run_id=_generate_id(),
        scope_id=_generate_id(),
        workspace_root=Path(environment_value.workspace_root).expanduser().resolve(),
    )

    from ..tools.registry import tool_scope

    with tool_scope():
        token = _environment_var.set(resolved)
        try:
            with span(
                "nighthawk.run",
                **{
                    RUN_ID: resolved.run_id,
                    SCOPE_ID: resolved.scope_id,
                },
            ):
                yield
        finally:
            _environment_var.reset(token)


@contextmanager
def scope(
    *,
    workspace_root: str | Path | None = None,
    run_configuration: RunConfiguration | None = None,
    step_executor: StepExecutor | None = None,
    system_prompt_suffix_fragment: str | None = None,
    user_prompt_suffix_fragment: str | None = None,
) -> Iterator[Environment]:
    current = get_environment()

    next_environment = current

    if run_configuration is not None:
        next_environment = replace(next_environment, run_configuration=run_configuration)

    if workspace_root is not None:
        resolved_root = Path(workspace_root).expanduser().resolve()  # type: ignore[arg-type]
        next_environment = replace(next_environment, workspace_root=resolved_root)

    if step_executor is not None:
        next_environment = replace(next_environment, step_executor=step_executor)

    if system_prompt_suffix_fragment is not None:
        next_environment = replace(
            next_environment,
            system_prompt_suffix_fragments=(
                *next_environment.system_prompt_suffix_fragments,
                system_prompt_suffix_fragment,
            ),
        )

    if user_prompt_suffix_fragment is not None:
        next_environment = replace(
            next_environment,
            user_prompt_suffix_fragments=(
                *next_environment.user_prompt_suffix_fragments,
                user_prompt_suffix_fragment,
            ),
        )

    next_environment = replace(next_environment, scope_id=_generate_id())

    from ..tools.registry import tool_scope

    with tool_scope():
        token = _environment_var.set(next_environment)
        try:
            with span(
                "nighthawk.scope",
                **{
                    RUN_ID: next_environment.run_id,
                    SCOPE_ID: next_environment.scope_id,
                },
            ):
                yield next_environment
        finally:
            _environment_var.reset(token)


__all__ = [
    "RUN_ID",
    "SCOPE_ID",
    "STEP_ID",
    "TOOL_CALL_ID",
    "get_environment",
    "run",
    "scope",
    "span",
]
