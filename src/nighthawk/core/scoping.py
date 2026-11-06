from __future__ import annotations

import importlib.metadata
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Span, get_tracer_provider

from nighthawk._identifiers import generate_ulid
from nighthawk.core.configuration import UNSET, Merge, UnsetType
from nighthawk.core.errors import NameConflictError
from nighthawk.core.execution_reference import ExecutionReference
from nighthawk.core.model_selection import ModelSelection
from nighthawk.core.usage import UsageMeter
from nighthawk.errors import NighthawkError

if TYPE_CHECKING:
    from nighthawk.core.lifecycle import StepLifecycle
    from nighthawk.core.oversight import Oversight
    from nighthawk.core.step_executor import StepExecutor

RUN_ID = "run.id"
SCOPE_ID = "scope.id"
STEP_EXECUTION_ID = "step.execution.id"
SOURCE_LOCATION = "step.source_location"
_tracer = get_tracer_provider().get_tracer("nighthawk", importlib.metadata.version("nighthawk-python"))


@dataclass(frozen=True, kw_only=True)
class Scope:
    """Live execution selection; application values retain identity."""

    execution_reference: ExecutionReference
    # Caller-supplied run identifiers may repeat; adapters isolate nested runs by object identity.
    _run_identity: object = field(default_factory=object, repr=False, compare=False)
    step_executor: StepExecutor
    usage_meter: UsageMeter
    oversight: Oversight | None = None
    lifecycle: StepLifecycle | None = None
    task_references: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


_scope_var: ContextVar[Scope | None] = ContextVar("nighthawk_scope", default=None)


@contextmanager
def span(span_name: str, /, **attributes: Any) -> Generator[Span]:
    with _tracer.start_as_current_span(
        span_name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current_span:
        yield current_span


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


def _merge_task_references(current: Mapping[str, object], supplied: Mapping[str, object]) -> Mapping[str, object]:
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
    from nighthawk.core.step_executor import AsyncStepExecutor, ConfigurableStepExecutor, SyncStepExecutor

    if not isinstance(step_executor, ConfigurableStepExecutor):
        raise NighthawkError("Current step_executor must implement ConfigurableStepExecutor to resolve model selection")
    resolved = step_executor.resolve_model_selection(selection)
    if not isinstance(resolved, (AsyncStepExecutor, SyncStepExecutor)):
        raise NighthawkError("Model selection resolver must return a StepExecutor")
    return resolved


def _validate_step_executor(step_executor: object) -> None:
    from nighthawk.core.step_executor import AsyncStepExecutor, SyncStepExecutor

    if not isinstance(step_executor, (AsyncStepExecutor, SyncStepExecutor)):
        raise TypeError("step_executor must implement the step executor protocol")


@contextmanager
def run(
    step_executor: StepExecutor,
    *,
    model_selection: ModelSelection | None = None,
    run_id: str | UnsetType = UNSET,
    usage_meter: UsageMeter | UnsetType = UNSET,
) -> Generator[Scope]:
    """Start an independent execution scope using the supplied executor.

    Nested runs create fresh identities and controls; exiting restores the caller's scope.
    Model selection is resolved by the executor before the scope is installed.
    """
    if not isinstance(run_id, (str, UnsetType)):
        raise TypeError("run_id must be a nonempty string or UNSET")
    if isinstance(run_id, str) and not run_id:
        raise ValueError("run_id must not be empty")
    if not isinstance(usage_meter, (UsageMeter, UnsetType)):
        raise TypeError("usage_meter must be a UsageMeter or UNSET")
    _validate_step_executor(step_executor)
    if model_selection is not None:
        step_executor = _resolve_model_selection(step_executor, model_selection)
    selected = Scope(
        execution_reference=ExecutionReference(
            run_id=generate_ulid() if isinstance(run_id, UnsetType) else run_id,
            scope_id=generate_ulid(),
        ),
        step_executor=step_executor,
        usage_meter=UsageMeter() if isinstance(usage_meter, UnsetType) else usage_meter,
    )
    token = _scope_var.set(selected)
    try:
        with span("nighthawk.run", **{RUN_ID: selected.execution_reference.run_id}):
            yield selected
    finally:
        _scope_var.reset(token)


@contextmanager
def scope(
    *,
    step_executor: StepExecutor | UnsetType = UNSET,
    model_selection: ModelSelection | None = None,
    usage_meter: UsageMeter | UnsetType = UNSET,
    oversight: Oversight | None | UnsetType = UNSET,
    lifecycle: StepLifecycle | None | UnsetType = UNSET,
    task_references: Mapping[str, object] | Merge[object] | UnsetType = UNSET,
) -> Generator[Scope]:
    """Derive execution controls and restore them on exit.

    UNSET inherits, ordinary values replace, and None clears hooks.
    Merge combines references by identity. Selection never bypasses the active executor.
    """
    from nighthawk.core.lifecycle import StepLifecycle
    from nighthawk.core.oversight import Oversight

    current = get_scope()
    if not isinstance(step_executor, UnsetType):
        _validate_step_executor(step_executor)
    if not isinstance(usage_meter, (UnsetType, UsageMeter)):
        raise TypeError("usage_meter must be UsageMeter or UNSET")
    if oversight is not None and not isinstance(oversight, (UnsetType, Oversight)):
        raise TypeError("oversight must be Oversight, None, or UNSET")
    if lifecycle is not None and not isinstance(lifecycle, (UnsetType, StepLifecycle)):
        raise TypeError("lifecycle must be StepLifecycle, None, or UNSET")
    references = current.task_references
    if isinstance(task_references, Merge):
        references = _merge_task_references(references, task_references.name_to_value)
    elif not isinstance(task_references, UnsetType):
        if not isinstance(task_references, Mapping) or any(not isinstance(name, str) for name in task_references):
            raise TypeError("task_references requires a string-keyed mapping, Merge, or UNSET")
        references = MappingProxyType(dict(task_references))
    executor = current.step_executor if isinstance(step_executor, UnsetType) else step_executor
    if model_selection is not None:
        executor = _resolve_model_selection(executor, model_selection)
    selected = replace(
        current,
        execution_reference=replace(current.execution_reference, scope_id=generate_ulid()),
        step_executor=executor,
        usage_meter=current.usage_meter if isinstance(usage_meter, UnsetType) else usage_meter,
        oversight=current.oversight if isinstance(oversight, UnsetType) else oversight,
        lifecycle=current.lifecycle if isinstance(lifecycle, UnsetType) else lifecycle,
        task_references=references,
    )
    token = _scope_var.set(selected)
    try:
        with span("nighthawk.scope", **{RUN_ID: selected.execution_reference.run_id, SCOPE_ID: selected.execution_reference.scope_id}):
            yield selected
    finally:
        _scope_var.reset(token)
