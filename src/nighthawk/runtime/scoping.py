from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Iterator

import logfire

from ..configuration import StepExecutorConfiguration, StepExecutorConfigurationPatch
from ..errors import NighthawkError
from .execution_context import ExecutionContext

if TYPE_CHECKING:
    from .step_executor import AgentStepExecutor, StepExecutor


RUN_ID = "run.id"
SCOPE_ID = "scope.id"
STEP_ID = "step.id"
TOOL_CALL_ID = "tool_call.id"


@contextmanager
def span(span_name: str, /, **attributes: Any) -> Iterator[None]:
    with logfire.span(span_name, **attributes):
        yield


def _generate_id() -> str:
    return uuid.uuid4().hex


_step_executor_var: ContextVar[StepExecutor | None] = ContextVar(
    "nighthawk_step_executor",
    default=None,
)

_execution_context_var: ContextVar[ExecutionContext | None] = ContextVar(
    "nighthawk_execution_context",
    default=None,
)


@dataclass(frozen=True)
class _PromptSuffixFragments:
    system_prompt_suffix_fragments: tuple[str, ...] = ()
    user_prompt_suffix_fragments: tuple[str, ...] = ()


_prompt_suffix_fragments_var: ContextVar[_PromptSuffixFragments] = ContextVar(
    "nighthawk_prompt_suffix_fragments",
    default=_PromptSuffixFragments(),
)


def get_step_executor() -> StepExecutor:
    step_executor = _step_executor_var.get()
    if step_executor is None:
        raise NighthawkError("StepExecutor is not set")
    return step_executor


def get_execution_context() -> ExecutionContext:
    execution_context = _execution_context_var.get()
    if execution_context is None:
        raise NighthawkError("ExecutionContext is not set")
    return execution_context


def get_system_prompt_suffix_fragments() -> tuple[str, ...]:
    return _prompt_suffix_fragments_var.get().system_prompt_suffix_fragments


def get_user_prompt_suffix_fragments() -> tuple[str, ...]:
    return _prompt_suffix_fragments_var.get().user_prompt_suffix_fragments


def _resolve_agent_step_executor(step_executor: StepExecutor) -> AgentStepExecutor:
    from .step_executor import AgentStepExecutor

    if not isinstance(step_executor, AgentStepExecutor):
        raise NighthawkError("StepExecutor configuration updates require current step_executor to be AgentStepExecutor")
    return step_executor


def _replace_step_executor_with_configuration(
    step_executor: StepExecutor,
    *,
    configuration: StepExecutorConfiguration,
) -> StepExecutor:
    from .step_executor import AgentStepExecutor

    current_step_executor = _resolve_agent_step_executor(step_executor)

    if current_step_executor.agent_is_managed:
        return AgentStepExecutor.from_configuration(configuration=configuration)

    assert current_step_executor.agent is not None
    return AgentStepExecutor.from_agent(
        agent=current_step_executor.agent,
        configuration=configuration,
    )


@contextmanager
def run(
    step_executor: StepExecutor,
    *,
    run_id: str | None = None,
) -> Iterator[None]:
    execution_context = ExecutionContext(
        run_id=run_id or _generate_id(),
        scope_id=_generate_id(),
    )

    from ..tools.registry import tool_scope

    with tool_scope():
        step_executor_token = _step_executor_var.set(step_executor)
        execution_context_token = _execution_context_var.set(execution_context)
        prompt_suffix_fragments_token = _prompt_suffix_fragments_var.set(_PromptSuffixFragments())
        try:
            with span(
                "nighthawk.run",
                **{
                    RUN_ID: execution_context.run_id,
                    SCOPE_ID: execution_context.scope_id,
                },
            ):
                yield
        finally:
            _prompt_suffix_fragments_var.reset(prompt_suffix_fragments_token)
            _execution_context_var.reset(execution_context_token)
            _step_executor_var.reset(step_executor_token)


@contextmanager
def scope(
    *,
    step_executor_configuration: StepExecutorConfiguration | None = None,
    step_executor_configuration_patch: StepExecutorConfigurationPatch | None = None,
    step_executor: StepExecutor | None = None,
    system_prompt_suffix_fragment: str | None = None,
    user_prompt_suffix_fragment: str | None = None,
) -> Iterator[StepExecutor]:
    current_step_executor = get_step_executor()
    current_execution_context = get_execution_context()

    next_step_executor = current_step_executor

    if step_executor is not None:
        next_step_executor = step_executor

    has_configuration_update = any(
        value is not None
        for value in (
            step_executor_configuration,
            step_executor_configuration_patch,
        )
    )

    if has_configuration_update:
        current_agent_step_executor = _resolve_agent_step_executor(next_step_executor)
        next_configuration = current_agent_step_executor.configuration

        if step_executor_configuration is not None:
            next_configuration = step_executor_configuration

        if step_executor_configuration_patch is not None:
            next_configuration = step_executor_configuration_patch.apply_to(next_configuration)

        next_step_executor = _replace_step_executor_with_configuration(
            next_step_executor,
            configuration=next_configuration,
        )

    next_execution_context = replace(
        current_execution_context,
        scope_id=_generate_id(),
    )
    current_prompt_suffix_fragments = _prompt_suffix_fragments_var.get()
    next_prompt_suffix_fragments = current_prompt_suffix_fragments

    if system_prompt_suffix_fragment is not None:
        next_prompt_suffix_fragments = replace(
            next_prompt_suffix_fragments,
            system_prompt_suffix_fragments=(
                *next_prompt_suffix_fragments.system_prompt_suffix_fragments,
                system_prompt_suffix_fragment,
            ),
        )

    if user_prompt_suffix_fragment is not None:
        next_prompt_suffix_fragments = replace(
            next_prompt_suffix_fragments,
            user_prompt_suffix_fragments=(
                *next_prompt_suffix_fragments.user_prompt_suffix_fragments,
                user_prompt_suffix_fragment,
            ),
        )

    from ..tools.registry import tool_scope

    with tool_scope():
        step_executor_token = _step_executor_var.set(next_step_executor)
        execution_context_token = _execution_context_var.set(next_execution_context)
        prompt_suffix_fragments_token = _prompt_suffix_fragments_var.set(next_prompt_suffix_fragments)
        try:
            with span(
                "nighthawk.scope",
                **{
                    RUN_ID: next_execution_context.run_id,
                    SCOPE_ID: next_execution_context.scope_id,
                },
            ):
                yield next_step_executor
        finally:
            _prompt_suffix_fragments_var.reset(prompt_suffix_fragments_token)
            _execution_context_var.reset(execution_context_token)
            _step_executor_var.reset(step_executor_token)


__all__ = [
    "RUN_ID",
    "SCOPE_ID",
    "STEP_ID",
    "TOOL_CALL_ID",
    "get_step_executor",
    "get_execution_context",
    "get_system_prompt_suffix_fragments",
    "get_user_prompt_suffix_fragments",
    "run",
    "scope",
    "span",
]
