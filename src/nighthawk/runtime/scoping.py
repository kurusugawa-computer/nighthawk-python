from __future__ import annotations

import importlib.metadata
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Span, get_tracer_provider
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.usage import RunUsage

from ..composition import UNSET, Extend, Merge, UnsetType
from ..configuration import StepExecutorConfiguration
from ..errors import NameConflictError, NighthawkError
from ..tools.declarations import ToolEntry, get_scoped_tools, resolve_scoped_tools, scoped_tools_var
from ..ulid import generate_ulid

if TYPE_CHECKING:
    from pydantic_ai.tools import Tool

    from ..oversight import Oversight
    from .step_context import StepContext
    from .step_executor import AgentStepExecutor, StepExecutor


@dataclass(frozen=True)
class ExecutionRef:
    run_id: str
    scope_id: str
    step_id: str | None = None


class UsageMeter:
    """Accumulates LLM token usage across all steps in a run.

    Thread-safe. Created automatically by :func:`run` and accessible via :func:`get_usage_meter`.
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

_capabilities_var: ContextVar[tuple[AbstractCapability[StepContext], ...]] = ContextVar(
    "nighthawk_capabilities",
    default=(),
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
                raise NameConflictError(
                    f"Conflict for implicit reference {implicit_reference_name!r}: "
                    f"current scope has {current_implicit_reference_type_name}, "
                    f"new scope has {scope_implicit_reference_type_name}"
                )

        merged_implicit_reference_name_to_value[implicit_reference_name] = scope_implicit_reference_value

    return merged_implicit_reference_name_to_value


def _require_active_run(function_name: str) -> None:
    if _step_executor_var.get() is None:
        raise NighthawkError(f"{function_name} requires an active nighthawk.run() context")


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


def _optional_usage_meter() -> UsageMeter | None:
    return _usage_meter_var.get()


def get_usage_meter() -> UsageMeter:
    """Return the active usage meter; require an active run."""
    _require_active_run("get_usage_meter")
    meter = _usage_meter_var.get()
    assert meter is not None
    return meter


def get_oversight() -> Oversight | None:
    """Return the oversight hooks active in the current scope, or ``None`` if none are installed."""
    _require_active_run("get_oversight")
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


def _current_system_prompt_suffix_fragments() -> tuple[str, ...]:
    return _system_prompt_suffix_fragments_var.get()


def _current_user_prompt_suffix_fragments() -> tuple[str, ...]:
    return _user_prompt_suffix_fragments_var.get()


def _current_implicit_references() -> Mapping[str, object]:
    return dict(_implicit_reference_name_to_value_var.get())


def _current_capabilities() -> tuple[AbstractCapability[StepContext], ...]:
    return _capabilities_var.get()


def get_system_prompt_suffix_fragments() -> tuple[str, ...]:
    """Return the system prompt suffix fragments active in the current scope.

    Configuration-level baseline fragments from ``StepExecutorConfiguration``
    are not included; only fragments accumulated via ``scope`` are returned.

    Raises:
        NighthawkError: If called outside a run context.
    """
    _require_active_run("get_system_prompt_suffix_fragments")
    return _current_system_prompt_suffix_fragments()


def get_user_prompt_suffix_fragments() -> tuple[str, ...]:
    """Return the user prompt suffix fragments active in the current scope.

    Configuration-level baseline fragments from ``StepExecutorConfiguration``
    are not included; only fragments accumulated via ``scope`` are returned.

    Raises:
        NighthawkError: If called outside a run context.
    """
    _require_active_run("get_user_prompt_suffix_fragments")
    return _current_user_prompt_suffix_fragments()


def get_implicit_references() -> Mapping[str, object]:
    """Return the implicit references active in the current scope.

    The returned mapping is an independent snapshot; mutating it does not
    affect the active scope.

    Raises:
        NighthawkError: If called outside a run context.
    """
    _require_active_run("get_implicit_references")
    return _current_implicit_references()


def get_tools() -> tuple[Tool[StepContext], ...]:
    """Return the tools declared for the current scope, excluding built-in tools.

    Raises:
        NighthawkError: If called outside a run context.
    """
    _require_active_run("get_tools")
    return get_scoped_tools()


def get_capabilities() -> tuple[AbstractCapability[StepContext], ...]:
    """Return the Pydantic AI capabilities active in the current scope.

    Capabilities are passed to ``Agent.run(capabilities=...)`` on every model
    request made by an :class:`AgentStepExecutor` within the scope.

    Raises:
        NighthawkError: If called outside a run context.
    """
    _require_active_run("get_capabilities")
    return _current_capabilities()


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

    if not isinstance(configuration.model, str) or configuration.model != current_step_executor.configuration.model:
        raise NighthawkError("The external agent owns model selection; scope cannot switch its model")
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
    usage_meter: UsageMeter | UnsetType = UNSET,
) -> Iterator[None]:
    """Start an execution run with the given step executor.

    Establishes a run-scoped context that makes the step executor
    available to all Natural blocks executed within this scope.

    Args:
        step_executor: The step executor to use for Natural block execution.
        run_id: Optional identifier for the run. If not provided, a ULID is
            generated automatically.
        usage_meter: Optional meter that accumulates LLM usage for the run.
            If not provided, a fresh :class:`UsageMeter` is created.

    Yields:
        None

    Example:
        ```python
        executor = AgentStepExecutor.from_configuration(
            configuration=StepExecutorConfiguration(model="openai:gpt-5.6-terra"),
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
    if not isinstance(usage_meter, (UsageMeter, UnsetType)):
        raise TypeError("usage_meter must be a UsageMeter or UNSET")
    run_usage_meter = UsageMeter() if isinstance(usage_meter, UnsetType) else usage_meter

    step_executor_token = _step_executor_var.set(step_executor)
    execution_ref_token = _execution_ref_var.set(execution_ref)
    oversight_token = _oversight_var.set(None)
    system_fragments_token = _system_prompt_suffix_fragments_var.set(())
    user_fragments_token = _user_prompt_suffix_fragments_var.set(())
    implicit_reference_name_to_value_token = _implicit_reference_name_to_value_var.set({})
    tools_token = scoped_tools_var.set(())
    capabilities_token = _capabilities_var.set(())
    usage_meter_token = _usage_meter_var.set(run_usage_meter)
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
        _capabilities_var.reset(capabilities_token)
        scoped_tools_var.reset(tools_token)
        _implicit_reference_name_to_value_var.reset(implicit_reference_name_to_value_token)
        _user_prompt_suffix_fragments_var.reset(user_fragments_token)
        _system_prompt_suffix_fragments_var.reset(system_fragments_token)
        _oversight_var.reset(oversight_token)
        _execution_ref_var.reset(execution_ref_token)
        _step_executor_var.reset(step_executor_token)


def _compose_sequence[T](current: tuple[T, ...], supplied: Sequence[T] | Extend[T] | UnsetType) -> tuple[T, ...]:
    if isinstance(supplied, UnsetType):
        return current
    if isinstance(supplied, Extend):
        return (*current, *supplied.values)
    if not isinstance(supplied, Sequence) or isinstance(supplied, (str, bytes, bytearray)):
        raise TypeError("Scope collections require a sequence, Extend, or UNSET")
    return tuple(supplied)


@contextmanager
def scope(
    *,
    step_executor_configuration: StepExecutorConfiguration | UnsetType = UNSET,
    step_executor: StepExecutor | UnsetType = UNSET,
    usage_meter: UsageMeter | UnsetType = UNSET,
    oversight: Oversight | None | UnsetType = UNSET,
    system_prompt_suffix_fragments: Sequence[str] | Extend[str] | UnsetType = UNSET,
    user_prompt_suffix_fragments: Sequence[str] | Extend[str] | UnsetType = UNSET,
    implicit_references: Mapping[str, object] | Merge[object] | UnsetType = UNSET,
    tools: Sequence[ToolEntry] | Extend[ToolEntry] | UnsetType = UNSET,
    capabilities: Sequence[AbstractCapability[StepContext]] | Extend[AbstractCapability[StepContext]] | UnsetType = UNSET,
) -> Iterator[StepExecutor]:
    """Compose a nested execution scope and restore its parent on exit.

    Omission or UNSET inherits. Ordinary values replace; empty collections clear.
    Extend appends ordered entries; Merge combines implicit references by identity.
    Only oversight accepts None, which clears its hooks. Resolve all changes before
    installing context. Executor replacement precedes full configuration replacement.
    """
    from ..oversight import Oversight
    from .step_executor import AsyncStepExecutor, SyncStepExecutor

    current_step_executor = get_step_executor()
    current_execution_ref = get_execution_ref()
    if not isinstance(step_executor, (UnsetType, AsyncStepExecutor, SyncStepExecutor)):
        raise TypeError("step_executor must implement the step executor protocol or be UNSET")
    if not isinstance(step_executor_configuration, (UnsetType, StepExecutorConfiguration)):
        raise TypeError("step_executor_configuration must be StepExecutorConfiguration or UNSET")
    if not isinstance(usage_meter, (UnsetType, UsageMeter)):
        raise TypeError("usage_meter must be UsageMeter or UNSET")
    if oversight is not None and not isinstance(oversight, (UnsetType, Oversight)):
        raise TypeError("oversight must be Oversight, None, or UNSET")
    next_step_executor = current_step_executor if isinstance(step_executor, UnsetType) else step_executor
    if not isinstance(step_executor_configuration, UnsetType):
        next_step_executor = _replace_step_executor_with_configuration(next_step_executor, configuration=step_executor_configuration)
    next_execution_ref = replace(current_execution_ref, scope_id=generate_ulid(), step_id=None)
    next_usage_meter = get_usage_meter() if isinstance(usage_meter, UnsetType) else usage_meter
    next_oversight = _oversight_var.get() if isinstance(oversight, UnsetType) else oversight
    next_system_prompt_suffix_fragments = _compose_sequence(_system_prompt_suffix_fragments_var.get(), system_prompt_suffix_fragments)
    next_user_prompt_suffix_fragments = _compose_sequence(_user_prompt_suffix_fragments_var.get(), user_prompt_suffix_fragments)
    if any(not isinstance(fragment, str) for fragment in (*next_system_prompt_suffix_fragments, *next_user_prompt_suffix_fragments)):
        raise TypeError("Prompt fragments must be strings")
    next_capabilities = _compose_sequence(_capabilities_var.get(), capabilities)
    if any(not isinstance(capability, AbstractCapability) for capability in next_capabilities):
        raise TypeError("Capabilities must be AbstractCapability instances")
    next_implicit_reference_name_to_value = _implicit_reference_name_to_value_var.get()
    if isinstance(implicit_references, Merge):
        next_implicit_reference_name_to_value = _merge_implicit_reference_name_to_value_with_conflict_check(
            next_implicit_reference_name_to_value, implicit_references.name_to_value
        )
    elif not isinstance(implicit_references, UnsetType):
        if not isinstance(implicit_references, Mapping) or any(not isinstance(name, str) for name in implicit_references):
            raise TypeError("implicit_references requires a string-keyed mapping, Merge, or UNSET")
        next_implicit_reference_name_to_value = dict(implicit_references)
    next_tools = scoped_tools_var.get()
    if isinstance(tools, Extend):
        next_tools = resolve_scoped_tools(next_tools, tools.values)
    elif not isinstance(tools, UnsetType):
        entries = _compose_sequence((), tools)
        next_tools = resolve_scoped_tools((), entries)

    step_executor_token = _step_executor_var.set(next_step_executor)
    execution_ref_token = _execution_ref_var.set(next_execution_ref)
    usage_meter_token = _usage_meter_var.set(next_usage_meter)
    oversight_token = _oversight_var.set(next_oversight)
    system_fragments_token = _system_prompt_suffix_fragments_var.set(next_system_prompt_suffix_fragments)
    user_fragments_token = _user_prompt_suffix_fragments_var.set(next_user_prompt_suffix_fragments)
    implicit_reference_name_to_value_token = _implicit_reference_name_to_value_var.set(next_implicit_reference_name_to_value)
    tools_token = scoped_tools_var.set(next_tools)
    capabilities_token = _capabilities_var.set(next_capabilities)
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
        _capabilities_var.reset(capabilities_token)
        scoped_tools_var.reset(tools_token)
        _implicit_reference_name_to_value_var.reset(implicit_reference_name_to_value_token)
        _user_prompt_suffix_fragments_var.reset(user_fragments_token)
        _system_prompt_suffix_fragments_var.reset(system_fragments_token)
        _oversight_var.reset(oversight_token)
        _usage_meter_var.reset(usage_meter_token)
        _execution_ref_var.reset(execution_ref_token)
        _step_executor_var.reset(step_executor_token)
