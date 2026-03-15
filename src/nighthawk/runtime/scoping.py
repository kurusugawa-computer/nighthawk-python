from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Span, get_tracer_provider

from ..configuration import StepExecutorConfiguration, StepExecutorConfigurationPatch
from ..errors import NighthawkError
from ..tools.registry import tool_scope

if TYPE_CHECKING:
    from .step_executor import AgentStepExecutor, StepExecutor


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable snapshot of the current execution context.

    Attributes:
        run_id: Unique identifier for the run.
        scope_id: Unique identifier for the current scope.
    """

    run_id: str
    scope_id: str


RUN_ID = "run.id"
SCOPE_ID = "scope.id"
STEP_ID = "step.id"
TOOL_CALL_ID = "tool_call.id"


_tracer = get_tracer_provider().get_tracer("nighthawk")


@contextmanager
def span(span_name: str, /, **attributes: Any) -> Iterator[Span]:
    with _tracer.start_as_current_span(
        span_name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current_span:
        yield current_span


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


_system_prompt_suffix_fragments_var: ContextVar[tuple[str, ...]] = ContextVar(
    "nighthawk_system_prompt_suffix_fragments",
    default=(),
)

_user_prompt_suffix_fragments_var: ContextVar[tuple[str, ...]] = ContextVar(
    "nighthawk_user_prompt_suffix_fragments",
    default=(),
)


def get_step_executor() -> StepExecutor:
    """Return the active step executor.

    Raises:
        NighthawkError: If no step executor is set (i.e. called outside a run context).
    """
    step_executor = _step_executor_var.get()
    if step_executor is None:
        raise NighthawkError("StepExecutor is not set")
    return step_executor


def get_execution_context() -> ExecutionContext:
    """Return the active execution context.

    Raises:
        NighthawkError: If no execution context is set (i.e. called outside a run context).
    """
    execution_context = _execution_context_var.get()
    if execution_context is None:
        raise NighthawkError("ExecutionContext is not set")
    return execution_context


def get_system_prompt_suffix_fragments() -> tuple[str, ...]:
    return _system_prompt_suffix_fragments_var.get()


def get_user_prompt_suffix_fragments() -> tuple[str, ...]:
    return _user_prompt_suffix_fragments_var.get()


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

    if current_step_executor.agent is None:
        raise NighthawkError("AgentStepExecutor.agent is not initialized")
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
    """Start an execution run with the given step executor.

    Establishes a run-scoped context that makes the step executor
    available to all Natural blocks executed within this scope.

    Args:
        step_executor: The step executor to use for Natural block execution.
        run_id: Optional identifier for the run. If not provided, a UUID is
            generated automatically.

    Yields:
        None

    Example:
        ```python
        executor = AgentStepExecutor.from_configuration(
            configuration=StepExecutorConfiguration(model="openai:gpt-4o"),
        )
        with nighthawk.run(executor):
            result = my_natural_function()
        ```
    """
    execution_context = ExecutionContext(
        run_id=run_id or _generate_id(),
        scope_id=_generate_id(),
    )

    with tool_scope():
        step_executor_token = _step_executor_var.set(step_executor)
        execution_context_token = _execution_context_var.set(execution_context)
        system_fragments_token = _system_prompt_suffix_fragments_var.set(())
        user_fragments_token = _user_prompt_suffix_fragments_var.set(())
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
            _user_prompt_suffix_fragments_var.reset(user_fragments_token)
            _system_prompt_suffix_fragments_var.reset(system_fragments_token)
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
    """Open a nested scope that can override the step executor or its configuration.

    Must be called inside an active run context. Creates a new scope_id while
    inheriting the run_id from the parent context.

    Args:
        step_executor_configuration: Full replacement configuration for the step
            executor.
        step_executor_configuration_patch: Partial override applied on top of the
            current configuration.
        step_executor: Replacement step executor for this scope.
        system_prompt_suffix_fragment: Additional text appended to the system prompt.
        user_prompt_suffix_fragment: Additional text appended to the user prompt.

    Yields:
        The step executor active within this scope.

    Example:
        ```python
        with nighthawk.run(executor):
            with nighthawk.scope(
                step_executor_configuration_patch=StepExecutorConfigurationPatch(
                    model="openai:gpt-4o-mini",
                ),
            ) as scoped_executor:
                result = my_natural_function()
        ```
    """
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

    next_system_fragments = _system_prompt_suffix_fragments_var.get()
    next_user_fragments = _user_prompt_suffix_fragments_var.get()

    if system_prompt_suffix_fragment is not None:
        next_system_fragments = (*next_system_fragments, system_prompt_suffix_fragment)

    if user_prompt_suffix_fragment is not None:
        next_user_fragments = (*next_user_fragments, user_prompt_suffix_fragment)

    with tool_scope():
        step_executor_token = _step_executor_var.set(next_step_executor)
        execution_context_token = _execution_context_var.set(next_execution_context)
        system_fragments_token = _system_prompt_suffix_fragments_var.set(next_system_fragments)
        user_fragments_token = _user_prompt_suffix_fragments_var.set(next_user_fragments)
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
            _user_prompt_suffix_fragments_var.reset(user_fragments_token)
            _system_prompt_suffix_fragments_var.reset(system_fragments_token)
            _execution_context_var.reset(execution_context_token)
            _step_executor_var.reset(step_executor_token)
