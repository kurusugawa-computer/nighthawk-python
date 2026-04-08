from __future__ import annotations

import importlib.metadata
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from opentelemetry.trace import Span, get_tracer_provider
from pydantic_ai.usage import RunUsage

from ..configuration import StepExecutorConfiguration
from ..errors import NighthawkError
from ..tools.registry import tool_scope
from ..ulid import generate_ulid

if TYPE_CHECKING:
    from ..oversight import Oversight
    from .step_executor import AgentStepExecutor, StepExecutor


@dataclass(frozen=True)
class ExecutionRef:
    run_id: str
    scope_id: str
    step_id: str | None = None


class UsageMeter:
    """Accumulates LLM token usage across all steps in a run.

    Thread-safe. Created automatically by :func:`run` and accessible via :func:`get_current_usage_meter`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cumulative = RunUsage()
        self._kind_name_to_cumulative_usage: dict[str, RunUsage] = {}

    def record(self, usage: RunUsage, *, kind: str = "default") -> None:
        """Add *usage* to the cumulative total and internal per-kind totals."""
        with self._lock:
            self._cumulative.incr(usage)
            kind_usage = self._kind_name_to_cumulative_usage.get(kind)
            if kind_usage is None:
                self._kind_name_to_cumulative_usage[kind] = copy(usage)
                return
            kind_usage.incr(usage)

    @property
    def total_tokens(self) -> int:
        """Cumulative total tokens (input + output) across all recorded steps."""
        with self._lock:
            return self._cumulative.total_tokens

    def snapshot(self) -> RunUsage:
        """Return an independent copy of the current cumulative usage."""
        with self._lock:
            return copy(self._cumulative)


RUN_ID = "run.id"
SCOPE_ID = "scope.id"
STEP_ID = "step.id"
TOOL_CALL_ID = "tool_call.id"


_LIBRARY_VERSION = importlib.metadata.version("nighthawk-python")

_tracer = get_tracer_provider().get_tracer("nighthawk", _LIBRARY_VERSION)


@contextmanager
def span(span_name: str, /, **attributes: Any) -> Iterator[Span]:
    with _tracer.start_as_current_span(
        span_name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current_span:
        yield current_span


_step_executor_var: ContextVar[StepExecutor | None] = ContextVar(
    "nighthawk_step_executor",
    default=None,
)

_execution_ref_var: ContextVar[ExecutionRef | None] = ContextVar(
    "nighthawk_execution_ref",
    default=None,
)

_usage_meter_var: ContextVar[UsageMeter | None] = ContextVar(
    "nighthawk_usage_meter",
    default=None,
)


class _UnsetOversightType:
    pass


_UNSET_OVERSIGHT = _UnsetOversightType()

_oversight_var: ContextVar[Oversight | None] = ContextVar(
    "nighthawk_oversight",
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

_implicit_reference_name_to_value_var: ContextVar[dict[str, object]] = ContextVar(
    "nighthawk_implicit_reference_name_to_value",
    default={},  # noqa: B039
)

type ImplicitReferenceNameToValue = Mapping[str, object]


def _merge_implicit_reference_name_to_value_with_conflict_check(
    current_implicit_reference_name_to_value: dict[str, object],
    scope_implicit_reference_name_to_value: ImplicitReferenceNameToValue,
) -> dict[str, object]:
    merged_implicit_reference_name_to_value = dict(current_implicit_reference_name_to_value)
    for implicit_reference_name, scope_implicit_reference_value in scope_implicit_reference_name_to_value.items():
        if implicit_reference_name in merged_implicit_reference_name_to_value:
            current_implicit_reference_value = merged_implicit_reference_name_to_value[implicit_reference_name]
            if current_implicit_reference_value is not scope_implicit_reference_value:
                current_implicit_reference_type_name = type(current_implicit_reference_value).__name__
                scope_implicit_reference_type_name = type(scope_implicit_reference_value).__name__
                raise NighthawkError(
                    f"Conflict for implicit reference {implicit_reference_name!r}: "
                    f"current scope has {current_implicit_reference_type_name}, "
                    f"new scope has {scope_implicit_reference_type_name}"
                )

        merged_implicit_reference_name_to_value[implicit_reference_name] = scope_implicit_reference_value

    return merged_implicit_reference_name_to_value


def get_step_executor() -> StepExecutor:
    """Return the active step executor.

    Raises:
        NighthawkError: If no step executor is set (i.e. called outside a run context).
    """
    step_executor = _step_executor_var.get()
    if step_executor is None:
        raise NighthawkError("StepExecutor is not set")
    return step_executor


def get_execution_ref() -> ExecutionRef:
    """Return the active execution identity.

    Raises:
        NighthawkError: If no execution identity is set (i.e. called outside a run context).
    """
    execution_ref = _execution_ref_var.get()
    if execution_ref is None:
        raise NighthawkError("ExecutionRef is not set")
    return execution_ref


def get_current_usage_meter() -> UsageMeter | None:
    """Return the active usage meter, or ``None`` if outside a run context."""
    return _usage_meter_var.get()


def get_oversight() -> Oversight | None:
    return _oversight_var.get()


@contextmanager
def step_execution_ref_scope(*, step_id: str) -> Iterator[ExecutionRef]:
    current_execution_ref = get_execution_ref()
    step_execution_ref = replace(current_execution_ref, step_id=step_id)
    step_execution_ref_token = _execution_ref_var.set(step_execution_ref)
    try:
        yield step_execution_ref
    finally:
        _execution_ref_var.reset(step_execution_ref_token)


def get_system_prompt_suffix_fragments() -> tuple[str, ...]:
    return _system_prompt_suffix_fragments_var.get()


def get_user_prompt_suffix_fragments() -> tuple[str, ...]:
    return _user_prompt_suffix_fragments_var.get()


def get_implicit_reference_name_to_value() -> dict[str, object]:
    return dict(_implicit_reference_name_to_value_var.get())


@contextmanager
def system_prompt_suffix_fragment_scope(fragment: str) -> Iterator[None]:
    current = _system_prompt_suffix_fragments_var.get()
    token = _system_prompt_suffix_fragments_var.set((*current, fragment))
    try:
        yield
    finally:
        _system_prompt_suffix_fragments_var.reset(token)


@contextmanager
def user_prompt_suffix_fragment_scope(fragment: str) -> Iterator[None]:
    current = _user_prompt_suffix_fragments_var.get()
    token = _user_prompt_suffix_fragments_var.set((*current, fragment))
    try:
        yield
    finally:
        _user_prompt_suffix_fragments_var.reset(token)


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
        run_id: Optional identifier for the run. If not provided, a ULID is
            generated automatically.

    Yields:
        None

    Example:
        ```python
        executor = AgentStepExecutor.from_configuration(
            configuration=StepExecutorConfiguration(model="openai:gpt-5.4"),
        )
        with nighthawk.run(executor):
            result = my_natural_function()
        ```
    """
    execution_ref = ExecutionRef(
        run_id=run_id or generate_ulid(),
        scope_id=generate_ulid(),
        step_id=None,
    )
    usage_meter = UsageMeter()

    with tool_scope():
        step_executor_token = _step_executor_var.set(step_executor)
        execution_ref_token = _execution_ref_var.set(execution_ref)
        oversight_token = _oversight_var.set(None)
        system_fragments_token = _system_prompt_suffix_fragments_var.set(())
        user_fragments_token = _user_prompt_suffix_fragments_var.set(())
        implicit_reference_name_to_value_token = _implicit_reference_name_to_value_var.set({})
        usage_meter_token = _usage_meter_var.set(usage_meter)
        try:
            with span(
                "nighthawk.run",
                **{
                    RUN_ID: execution_ref.run_id,
                },
            ):
                yield
        finally:
            _usage_meter_var.reset(usage_meter_token)
            _implicit_reference_name_to_value_var.reset(implicit_reference_name_to_value_token)
            _user_prompt_suffix_fragments_var.reset(user_fragments_token)
            _system_prompt_suffix_fragments_var.reset(system_fragments_token)
            _oversight_var.reset(oversight_token)
            _execution_ref_var.reset(execution_ref_token)
            _step_executor_var.reset(step_executor_token)


@contextmanager
def scope(
    *,
    mode: Literal["inherit", "replace"] = "inherit",
    step_executor_configuration: StepExecutorConfiguration | None = None,
    step_executor: StepExecutor | None = None,
    oversight: Oversight | None | _UnsetOversightType = _UNSET_OVERSIGHT,
    system_prompt_suffix_fragments: Sequence[str] | None = None,
    user_prompt_suffix_fragments: Sequence[str] | None = None,
    implicit_references: ImplicitReferenceNameToValue | None = None,
) -> Iterator[StepExecutor]:
    """Open a nested scope that can override the active execution identity.

    Must be called inside an active run context. Creates a new ``scope_id`` while inheriting the ``run_id`` from the parent identity.

    Args:
        mode: Scope composition mode. ``"inherit"`` appends/merges values into the current scope. ``"replace"`` fully replaces provided list/dict values.
            In both modes, ``None`` means no change except for ``oversight``, where omitted means no change and explicit ``None`` clears the active oversight.
        step_executor_configuration: Full replacement configuration for the step executor.
        step_executor: Replacement step executor for this scope.
        oversight: Scope-level oversight hooks. Omit to inherit the current oversight. Pass ``None`` to clear it for the nested scope.
        system_prompt_suffix_fragments: Additional system prompt suffix fragments.
            In ``mode="inherit"``, fragments are appended. In ``mode="replace"``, provided fragments fully replace the current fragments.
        user_prompt_suffix_fragments: Additional user prompt suffix fragments.
            In ``mode="inherit"``, fragments are appended. In ``mode="replace"``, provided fragments fully replace the current fragments.
        implicit_references: Implicit global references for this scope.
            In ``mode="inherit"``, values are merged with conflict checks. In ``mode="replace"``, provided mappings fully replace the current mapping.

    Yields:
        The step executor active within this scope.

    Example:
        ```python
        with nighthawk.run(executor):
            with nighthawk.scope(
                mode="replace",
                implicit_references={},
                system_prompt_suffix_fragments=["Use concise answers."],
            ) as scoped_executor:
                result = my_natural_function()
        ```
    """
    current_step_executor = get_step_executor()
    current_execution_ref = get_execution_ref()

    next_step_executor = current_step_executor
    if step_executor is not None:
        next_step_executor = step_executor

    if step_executor_configuration is not None:
        next_step_executor = _replace_step_executor_with_configuration(
            next_step_executor,
            configuration=step_executor_configuration,
        )

    next_execution_ref = replace(
        current_execution_ref,
        scope_id=generate_ulid(),
        step_id=None,
    )

    next_oversight = _oversight_var.get()
    next_system_prompt_suffix_fragments = _system_prompt_suffix_fragments_var.get()
    next_user_prompt_suffix_fragments = _user_prompt_suffix_fragments_var.get()
    next_implicit_reference_name_to_value = _implicit_reference_name_to_value_var.get()

    if not isinstance(oversight, _UnsetOversightType):
        next_oversight = oversight

    if system_prompt_suffix_fragments is not None:
        if mode == "inherit":
            next_system_prompt_suffix_fragments = (*next_system_prompt_suffix_fragments, *system_prompt_suffix_fragments)
        else:
            next_system_prompt_suffix_fragments = tuple(system_prompt_suffix_fragments)

    if user_prompt_suffix_fragments is not None:
        if mode == "inherit":
            next_user_prompt_suffix_fragments = (*next_user_prompt_suffix_fragments, *user_prompt_suffix_fragments)
        else:
            next_user_prompt_suffix_fragments = tuple(user_prompt_suffix_fragments)

    if implicit_references is not None:
        if mode == "inherit":
            next_implicit_reference_name_to_value = _merge_implicit_reference_name_to_value_with_conflict_check(
                next_implicit_reference_name_to_value,
                implicit_references,
            )
        else:
            next_implicit_reference_name_to_value = dict(implicit_references)

    with tool_scope():
        step_executor_token = _step_executor_var.set(next_step_executor)
        execution_ref_token = _execution_ref_var.set(next_execution_ref)
        oversight_token = _oversight_var.set(next_oversight)
        system_fragments_token = _system_prompt_suffix_fragments_var.set(next_system_prompt_suffix_fragments)
        user_fragments_token = _user_prompt_suffix_fragments_var.set(next_user_prompt_suffix_fragments)
        implicit_reference_name_to_value_token = _implicit_reference_name_to_value_var.set(next_implicit_reference_name_to_value)
        try:
            with span(
                "nighthawk.scope",
                **{
                    RUN_ID: next_execution_ref.run_id,
                    SCOPE_ID: next_execution_ref.scope_id,
                },
            ):
                yield next_step_executor
        finally:
            _implicit_reference_name_to_value_var.reset(implicit_reference_name_to_value_token)
            _user_prompt_suffix_fragments_var.reset(user_fragments_token)
            _system_prompt_suffix_fragments_var.reset(system_fragments_token)
            _oversight_var.reset(oversight_token)
            _execution_ref_var.reset(execution_ref_token)
            _step_executor_var.reset(step_executor_token)
