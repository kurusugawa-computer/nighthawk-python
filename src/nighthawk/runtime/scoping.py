from __future__ import annotations

import importlib.metadata
import threading
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Span, get_tracer_provider
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from ..composition import UNSET, Extend, Merge, UnsetType
from ..configuration import ModelSelection, StepExecutorConfiguration, _model_selection
from ..errors import NameConflictError, NighthawkError
from ..tools.declarations import ToolDeclaration, ToolEntry, resolve_scoped_tools
from ..ulid import generate_ulid
from .execution_reference import ExecutionReference

if TYPE_CHECKING:
    from pydantic_ai.tools import Tool

    from ..lifecycle import StepLifecycle
    from ..oversight import Oversight
    from .context import ContextBuilder
    from .step_context import StepContext
    from .step_executor import StepExecutor


class UsageMeter:
    """Accumulates LLM token usage across all steps in a run.

    Thread-safe. Created by :func:`run` and available as ``get_scope().usage_meter``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cumulative = RunUsage()

    def record(self, usage: RunUsage) -> None:
        """Add completed agent usage to the cumulative total."""
        with self._lock:
            self._cumulative.incr(usage)

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
STEP_EXECUTION_ID = "step.execution.id"
SOURCE_LOCATION = "step.source_location"
TOOL_CALL_ID = "tool_call.id"


_LIBRARY_VERSION = importlib.metadata.version("nighthawk-python")

_tracer = get_tracer_provider().get_tracer("nighthawk", _LIBRARY_VERSION)


@contextmanager
def span(span_name: str, /, **attributes: Any) -> Generator[Span]:
    with _tracer.start_as_current_span(
        span_name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current_span:
        yield current_span


@dataclass(frozen=True, kw_only=True)
class Scope:
    """The run and scope configuration observed by Natural blocks started here.

    ``run`` creates one and ``scope`` derives another; both yield it, and ``get_scope`` returns the active one.
    Inside a step, ``execution_reference`` carries the step identity.
    Collections are shallow snapshots; contained objects retain identity.
    """

    execution_reference: ExecutionReference
    step_executor: StepExecutor
    usage_meter: UsageMeter
    oversight: Oversight | None = None
    lifecycle: StepLifecycle | None = None
    context_builder: ContextBuilder
    system_prompt_suffix_fragments: tuple[str, ...] = ()
    user_prompt_suffix_fragments: tuple[str, ...] = ()
    task_name_to_value: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    capabilities: tuple[AbstractCapability[StepContext], ...] = ()
    _tool_declarations: tuple[ToolDeclaration, ...] = field(default=(), repr=False)

    @property
    def tools(self) -> tuple[Tool[StepContext], ...]:
        """Tools declared through ``scope(tools=...)``, excluding built-in tools."""
        return tuple(declaration.tool for declaration in self._tool_declarations)


_scope_var: ContextVar[Scope | None] = ContextVar("nighthawk_scope", default=None)


def get_scope() -> Scope:
    """Return the active scope.

    Raises:
        NighthawkError: If called outside ``nighthawk.run()``.
    """
    active_scope = _scope_var.get()
    if active_scope is None:
        raise NighthawkError("get_scope requires an active nighthawk.run() context")
    return active_scope


def optional_scope() -> Scope | None:
    return _scope_var.get()


@contextmanager
def _derived_scope(**changes: Any) -> Generator[Scope]:
    derived = replace(get_scope(), **changes)
    token = _scope_var.set(derived)
    try:
        yield derived
    finally:
        _scope_var.reset(token)


@contextmanager
def step_execution_reference_scope(*, source_location: str) -> Generator[ExecutionReference]:
    current_execution_reference = get_scope().execution_reference
    step_execution_reference = replace(
        current_execution_reference,
        step_execution_id=generate_ulid(),
        source_location=source_location,
        parent_step_execution_id=current_execution_reference.step_execution_id,
    )
    with _derived_scope(execution_reference=step_execution_reference):
        yield step_execution_reference


@contextmanager
def system_prompt_suffix_fragment_scope(fragment: str) -> Generator[None]:
    with _derived_scope(system_prompt_suffix_fragments=(*get_scope().system_prompt_suffix_fragments, fragment)):
        yield


def _merge_task_name_to_value(current: Mapping[str, object], supplied: Mapping[str, object]) -> Mapping[str, object]:
    merged_task_name_to_value = dict(current)
    for name, value in supplied.items():
        if name in merged_task_name_to_value and merged_task_name_to_value[name] is not value:
            raise NameConflictError(
                f"Conflict for task reference {name!r}: "
                f"current scope has {type(merged_task_name_to_value[name]).__name__}, "
                f"new scope has {type(value).__name__}"
            )
        merged_task_name_to_value[name] = value
    return MappingProxyType(merged_task_name_to_value)


def _resolve_model_selection(step_executor: StepExecutor, selection: ModelSelection) -> StepExecutor:
    from .step_executor import AsyncStepExecutor, ConfigurableStepExecutor, SyncStepExecutor

    if not isinstance(step_executor, ConfigurableStepExecutor):
        raise NighthawkError("Current step_executor must implement ConfigurableStepExecutor to resolve model selection")
    resolved = step_executor.resolve_model_selection(selection)
    if not isinstance(resolved, (AsyncStepExecutor, SyncStepExecutor)):
        raise NighthawkError("Model selection resolver must return a StepExecutor")
    return resolved


def _validate_step_executor(step_executor: object) -> None:
    from .step_executor import AsyncStepExecutor, SyncStepExecutor

    if not isinstance(step_executor, (AsyncStepExecutor, SyncStepExecutor)):
        raise TypeError("step_executor must implement the step executor protocol")


@contextmanager
def run(
    step_executor: StepExecutor | UnsetType = UNSET,
    *,
    execution_model: str | Model | None | UnsetType = UNSET,
    decision_model: str | Model | None | UnsetType = UNSET,
    step_executor_configuration: StepExecutorConfiguration | UnsetType = UNSET,
    run_id: str | UnsetType = UNSET,
    usage_meter: UsageMeter | UnsetType = UNSET,
) -> Generator[Scope]:
    """Start a run whose fresh scope makes the step executor available to Natural blocks.

    A run starts from default scope settings and a new execution identity; it does not inherit an enclosing scope.

    Args:
        step_executor: Optional custom executor; omission creates a managed AgentStepExecutor.
        execution_model: Model for execute blocks; omission leaves the executor selection unchanged.
        decision_model: Model for decide blocks; omission leaves the executor selection unchanged.
        step_executor_configuration: Full configuration replacement, exclusive with model selections.
        run_id: Nonempty identifier for the run; omission generates a fresh ULID.
        usage_meter: Meter that accumulates LLM usage for the run; omission creates a fresh one.

    Yields:
        The run's scope.

    Raises:
        TypeError: If the executor does not implement either execution protocol or the usage meter is invalid.
    """
    from .context import DefaultContextBuilder
    from .step_executor import AgentStepExecutor

    if not isinstance(run_id, (str, UnsetType)):
        raise TypeError("run_id must be a nonempty string or UNSET")
    if isinstance(run_id, str) and not run_id:
        raise ValueError("run_id must not be empty")
    if not isinstance(usage_meter, (UsageMeter, UnsetType)):
        raise TypeError("usage_meter must be a UsageMeter or UNSET")
    selection = _model_selection(execution_model, decision_model, step_executor_configuration)
    if isinstance(step_executor, UnsetType):
        step_executor = AgentStepExecutor()
    _validate_step_executor(step_executor)
    if selection is not None:
        step_executor = _resolve_model_selection(step_executor, selection)
    run_scope = Scope(
        execution_reference=ExecutionReference(
            run_id=generate_ulid() if isinstance(run_id, UnsetType) else run_id,
            scope_id=generate_ulid(),
        ),
        step_executor=step_executor,
        usage_meter=UsageMeter() if isinstance(usage_meter, UnsetType) else usage_meter,
        context_builder=DefaultContextBuilder(),
    )
    token = _scope_var.set(run_scope)
    try:
        with span("nighthawk.run", **{RUN_ID: run_scope.execution_reference.run_id}):
            yield run_scope
    finally:
        _scope_var.reset(token)


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
    execution_model: str | Model | None | UnsetType = UNSET,
    decision_model: str | Model | None | UnsetType = UNSET,
    step_executor_configuration: StepExecutorConfiguration | UnsetType = UNSET,
    step_executor: StepExecutor | UnsetType = UNSET,
    usage_meter: UsageMeter | UnsetType = UNSET,
    oversight: Oversight | None | UnsetType = UNSET,
    lifecycle: StepLifecycle | None | UnsetType = UNSET,
    system_prompt_suffix_fragments: Sequence[str] | Extend[str] | UnsetType = UNSET,
    user_prompt_suffix_fragments: Sequence[str] | Extend[str] | UnsetType = UNSET,
    task_name_to_value: Mapping[str, object] | Merge[object] | UnsetType = UNSET,
    context_builder: ContextBuilder | UnsetType = UNSET,
    tools: Sequence[ToolEntry] | Extend[ToolEntry] | UnsetType = UNSET,
    capabilities: Sequence[AbstractCapability[StepContext]] | Extend[AbstractCapability[StepContext]] | UnsetType = UNSET,
) -> Generator[Scope]:
    """Derive a nested scope from the active one and restore it on exit.

    Omission or UNSET inherits. Ordinary values replace; empty collections clear.
    Extend appends ordered entries; Merge combines task references by identity.
    Context builders replace presentation while preserving the live environment.
    Oversight and lifecycle accept None, which clears their hooks. Resolve all changes before
    installing context. Executor replacement precedes delegated model selection.
    execution_model and decision_model replace their respective models; step_executor_configuration replaces all configuration
    fields. Supplying both is invalid. Selection preserves unrelated scoped controls.
    A new scope retains the active step identity and its source location.

    Yields:
        The derived scope.
    """
    from ..lifecycle import StepLifecycle
    from ..oversight import Oversight
    from .context import ContextBuilder

    current = get_scope()
    if not isinstance(context_builder, (UnsetType, ContextBuilder)):
        raise TypeError("context_builder must implement ContextBuilder or be UNSET")
    if not isinstance(step_executor, UnsetType):
        _validate_step_executor(step_executor)
    if not isinstance(step_executor_configuration, (UnsetType, StepExecutorConfiguration)):
        raise TypeError("step_executor_configuration must be StepExecutorConfiguration or UNSET")
    if not isinstance(usage_meter, (UnsetType, UsageMeter)):
        raise TypeError("usage_meter must be UsageMeter or UNSET")
    if oversight is not None and not isinstance(oversight, (UnsetType, Oversight)):
        raise TypeError("oversight must be Oversight, None, or UNSET")
    if lifecycle is not None and not isinstance(lifecycle, (UnsetType, StepLifecycle)):
        raise TypeError("lifecycle must be StepLifecycle, None, or UNSET")
    selection = _model_selection(execution_model, decision_model, step_executor_configuration)
    next_system_prompt_suffix_fragments = _compose_sequence(current.system_prompt_suffix_fragments, system_prompt_suffix_fragments)
    next_user_prompt_suffix_fragments = _compose_sequence(current.user_prompt_suffix_fragments, user_prompt_suffix_fragments)
    if any(not isinstance(fragment, str) for fragment in (*next_system_prompt_suffix_fragments, *next_user_prompt_suffix_fragments)):
        raise TypeError("Prompt fragments must be strings")
    next_capabilities = _compose_sequence(current.capabilities, capabilities)
    if any(not isinstance(capability, AbstractCapability) for capability in next_capabilities):
        raise TypeError("Capabilities must be AbstractCapability instances")
    next_task_name_to_value = current.task_name_to_value
    if isinstance(task_name_to_value, Merge):
        next_task_name_to_value = _merge_task_name_to_value(next_task_name_to_value, task_name_to_value.name_to_value)
    elif not isinstance(task_name_to_value, UnsetType):
        if not isinstance(task_name_to_value, Mapping) or any(not isinstance(name, str) for name in task_name_to_value):
            raise TypeError("task_name_to_value requires a string-keyed mapping, Merge, or UNSET")
        next_task_name_to_value = MappingProxyType(dict(task_name_to_value))
    next_tool_declarations = current._tool_declarations
    if isinstance(tools, Extend):
        next_tool_declarations = resolve_scoped_tools(next_tool_declarations, tools.values)
    elif not isinstance(tools, UnsetType):
        next_tool_declarations = resolve_scoped_tools((), _compose_sequence((), tools))

    next_step_executor = current.step_executor if isinstance(step_executor, UnsetType) else step_executor
    if selection is not None:
        next_step_executor = _resolve_model_selection(next_step_executor, selection)

    next_scope = replace(
        current,
        execution_reference=replace(current.execution_reference, scope_id=generate_ulid()),
        step_executor=next_step_executor,
        usage_meter=current.usage_meter if isinstance(usage_meter, UnsetType) else usage_meter,
        oversight=current.oversight if isinstance(oversight, UnsetType) else oversight,
        lifecycle=current.lifecycle if isinstance(lifecycle, UnsetType) else lifecycle,
        context_builder=current.context_builder if isinstance(context_builder, UnsetType) else context_builder,
        system_prompt_suffix_fragments=next_system_prompt_suffix_fragments,
        user_prompt_suffix_fragments=next_user_prompt_suffix_fragments,
        task_name_to_value=next_task_name_to_value,
        capabilities=next_capabilities,
        _tool_declarations=next_tool_declarations,
    )
    token = _scope_var.set(next_scope)
    try:
        with span(
            "nighthawk.scope",
            **{
                RUN_ID: next_scope.execution_reference.run_id,
                SCOPE_ID: next_scope.execution_reference.scope_id,
            },
        ):
            yield next_scope
    finally:
        _scope_var.reset(token)
