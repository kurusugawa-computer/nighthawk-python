from __future__ import annotations

import functools
import inspect
import typing
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from types import FrameType
from typing import TypeAliasType, TypedDict, cast

from opentelemetry.trace import Span, Status, StatusCode
from pydantic import TypeAdapter

from ..composition import UnsetType
from ..errors import ExecutionError, NaturalParseError, NighthawkError
from ..lifecycle import FailureStage, StepCompleted, StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepRaised, StepStarted
from ..natural.blocks import parse_frontmatter, validate_frontmatter_deny
from ..oversight import (
    Accept,
    OversightRejectedError,
    Reject,
    Rewrite,
    StepCommit,
    record_oversight_decision,
)
from .annotations import MISSING_ANNOTATION, SourceAnnotation, resolve_annotation
from .async_bridge import run_coroutine_synchronously
from .environment import StepEnvironment, UnboundEnvironmentNameError
from .execution_reference import ExecutionReference
from .notifications import await_notification
from .scoping import (
    RUN_ID,
    SCOPE_ID,
    SOURCE_LOCATION,
    STEP_EXECUTION_ID,
    _current_implicit_references,
    get_execution_reference,
    get_lifecycle,
    get_oversight,
    span,
    step_execution_reference_scope,
)
from .step_context import (
    StepContext,
    ToolResultRenderingPolicy,
    get_python_cell_scope_stack,
    get_step_context_stack,
    step_context_scope,
)
from .step_contract import StepKind
from .step_executor import AsyncStepExecutor, StepExecutor, SyncStepExecutor
from .step_request import StepCandidate, StepRequest
from .step_result import Break, Continue, Pass, Raise, Return, StepResult


def _split_frontmatter(
    processed_natural_program: str,
) -> tuple[str, tuple[str, ...]]:
    """Parse frontmatter, validate deny directives, and return stripped program + denied kinds."""
    try:
        program_without_frontmatter, frontmatter = parse_frontmatter(processed_natural_program)
    except NaturalParseError as e:
        raise ExecutionError(str(e)) from e
    try:
        denied_step_kinds = validate_frontmatter_deny(frontmatter)
    except NaturalParseError as e:
        raise ExecutionError(str(e)) from e
    return program_without_frontmatter, denied_step_kinds


def _compute_allowed_step_kinds(is_in_loop: bool, denied_step_kinds: tuple[str, ...]) -> tuple[StepKind, ...]:
    base_allowed_kinds: list[StepKind] = ["pass", "return", "raise"]
    if is_in_loop:
        base_allowed_kinds.extend(["break", "continue"])
    return tuple(kind for kind in base_allowed_kinds if kind not in denied_step_kinds)


def _infer_binding_types_from_initial_values(
    binding_name_to_type: dict[str, object],
    step_locals: dict[str, object],
) -> None:
    for name, declared_type in binding_name_to_type.items():
        if declared_type is not MISSING_ANNOTATION:
            continue
        binding_name_to_type[name] = object
        if name not in step_locals:
            continue
        initial_value = step_locals[name]
        inferred_type = type(initial_value)
        if inferred_type is not object and inferred_type is not type(None):
            binding_name_to_type[name] = inferred_type


def _discover_implicit_type_alias_reference_names(
    *,
    step_locals: dict[str, object],
    step_globals: dict[str, object],
    input_binding_names: Iterable[str],
) -> frozenset[str]:
    discovered_names: set[str] = set()
    seen: set[int] = set()

    def _collect(annotation: object) -> None:
        if isinstance(annotation, TypeAliasType):
            name = annotation.__name__
            if name in step_globals and name not in step_locals:
                discovered_names.add(name)
            return

        if isinstance(annotation, str):
            resolved = step_globals.get(annotation)
            if isinstance(resolved, TypeAliasType) and annotation not in step_locals:
                discovered_names.add(annotation)
            return

        annotation_id = id(annotation)
        if annotation_id in seen:
            return
        seen.add(annotation_id)

        for arg in typing.get_args(annotation):
            _collect(arg)

    def _scan_callable(value: object) -> None:
        target = value.func if isinstance(value, functools.partial) else value
        try:
            hints = typing.get_type_hints(target, localns=step_globals)
        except Exception:
            try:
                signature = inspect.signature(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            hints = {}
            for parameter in signature.parameters.values():
                if parameter.annotation is not inspect.Parameter.empty:
                    hints[parameter.name] = parameter.annotation
            if signature.return_annotation is not inspect.Signature.empty:
                hints["return"] = signature.return_annotation

        for annotation in hints.values():
            _collect(annotation)

    for value in step_locals.values():
        if callable(value):
            _scan_callable(value)

    step_locals_keys = step_locals.keys()
    for name in input_binding_names:
        if name not in step_locals_keys and name in step_globals:
            value = step_globals[name]
            if callable(value):
                _scan_callable(value)

    return frozenset(discovered_names)


def _build_step_globals(
    python_globals: dict[str, object],
) -> dict[str, object]:
    step_globals: dict[str, object] = dict(python_globals)
    if "__builtins__" not in step_globals:
        step_globals["__builtins__"] = __builtins__
    return step_globals


class StepEnvelope(TypedDict):
    """Envelope returned by Runner.run_step / run_step_async."""

    outcome: StepResult
    input_bindings: dict[str, object]
    bindings: dict[str, object]


def _add_step_completed_event(*, step_span: Span, step_outcome_kind: str) -> None:
    step_span.add_event(
        "nighthawk.step.completed",
        {
            "nighthawk.step.outcome_kind": step_outcome_kind,
        },
    )


def _add_step_raised_event(*, step_span: Span, step_outcome: Raise) -> None:
    event_attributes: dict[str, str] = {
        "nighthawk.step.outcome_kind": step_outcome.kind,
        "nighthawk.step.raise_message": step_outcome.message,
    }
    event_attributes["nighthawk.step.raise_error_type"] = step_outcome.error_type.__qualname__
    step_span.add_event("nighthawk.step.raised", event_attributes)


def _record_internal_step_failure(*, step_span: Span, exception: BaseException) -> None:
    step_span.add_event(
        "nighthawk.step.failed",
        {
            "nighthawk.step.error_kind": type(exception).__name__,
            "nighthawk.step.error_message": str(exception),
        },
    )
    step_span.record_exception(exception)
    step_span.set_status(Status(status_code=StatusCode.ERROR, description=str(exception)))


@dataclass(frozen=True)
class _StepPreparation:
    """Result of preparing a Natural block for execution."""

    processed_program: str
    allowed_step_kinds: tuple[StepKind, ...]
    step_context: StepContext
    input_binding_name_to_value: dict[str, object]
    output_binding_names: tuple[str, ...]
    binding_name_to_type: Mapping[str, object]


def _build_source_location(*, caller_frame: FrameType) -> str:
    module_name = caller_frame.f_globals.get("__name__")
    if not isinstance(module_name, str) or not module_name:
        module_name = "<unknown_module>"
    return f"{module_name}:{caller_frame.f_lineno}"


class Runner:
    def __init__(self, step_executor: StepExecutor) -> None:
        self.step_executor = step_executor
        self.failure_stage: FailureStage = "preparation"
        self.failure_description: str | None = None
        self.preparation: _StepPreparation | None = None
        self.processed_natural_program: str | None = None
        self.allowed_step_kinds: tuple[StepKind, ...] | None = None
        self.input_binding_name_to_value: dict[str, object] | None = None
        self.validated_binding_name_to_value: dict[str, object] | None = None
        self.assigned_binding_name_to_value: dict[str, object] = {}
        self.attempted_outcome: StepResult | None = None
        self.outcome: StepResult | None = None
        self.raise_exception: BaseException | None = None
        self.context_stack = ExitStack()

    @contextmanager
    def _execution_scope(self, caller_frame: FrameType) -> Iterator[tuple[ExecutionReference, Span, StepLifecycle | None]]:
        with step_execution_reference_scope(source_location=_build_source_location(caller_frame=caller_frame)) as execution_reference:
            lifecycle = get_lifecycle()
            with (
                span(
                    "nighthawk.step",
                    **{
                        RUN_ID: execution_reference.run_id,
                        SCOPE_ID: execution_reference.scope_id,
                        STEP_EXECUTION_ID: execution_reference.step_execution_id or "",
                        SOURCE_LOCATION: execution_reference.source_location or "",
                    },
                ) as step_span,
                self.context_stack,
            ):
                yield execution_reference, step_span, lifecycle

    def _classify_finished(
        self, original_exception: BaseException | None, execution_reference: ExecutionReference, step_span: Span
    ) -> tuple[StepFinished, BaseException | None]:
        if self.preparation is not None and self.preparation.step_context.operation_failure_stage is not None:
            self.failure_stage = self.preparation.step_context.operation_failure_stage
        finished: StepFinished
        if isinstance(original_exception, Exception):
            rejection = original_exception if isinstance(original_exception, OversightRejectedError) else None
            finished = StepFailed(
                execution_reference=execution_reference,
                processed_natural_program=self.processed_natural_program,
                input_binding_name_to_value=self.input_binding_name_to_value,
                allowed_step_kinds=self.allowed_step_kinds,
                assigned_binding_name_to_value=self.assigned_binding_name_to_value,
                failure_stage="oversight_rejection" if rejection else self.failure_stage,
                original_exception=original_exception,
                attempted_outcome=self.attempted_outcome,
                validated_binding_name_to_value=self.validated_binding_name_to_value,
                inspection_subject=cast(typing.Literal["step_commit", "operation"], rejection.subject) if rejection else None,
                rejection_reason=rejection.reason if rejection else None,
            )
            _record_internal_step_failure(step_span=step_span, exception=original_exception)
        elif original_exception is not None:
            finished = StepInterrupted(
                execution_reference=execution_reference,
                processed_natural_program=self.processed_natural_program,
                input_binding_name_to_value=self.input_binding_name_to_value,
                allowed_step_kinds=self.allowed_step_kinds,
                assigned_binding_name_to_value=self.assigned_binding_name_to_value,
                failure_stage=self.failure_stage,
                original_exception=original_exception,
            )
            step_span.add_event("nighthawk.step.interrupted", {"nighthawk.step.stage": self.failure_stage})
            step_span.record_exception(original_exception)
        elif isinstance(self.outcome, Raise):
            assert self.raise_exception is not None
            finished = StepRaised(
                execution_reference=execution_reference,
                processed_natural_program=self.processed_natural_program,
                input_binding_name_to_value=self.input_binding_name_to_value,
                allowed_step_kinds=self.allowed_step_kinds,
                assigned_binding_name_to_value=self.assigned_binding_name_to_value,
                outcome=self.outcome,
                exception=self.raise_exception,
            )
            original_exception = self.raise_exception
            _add_step_raised_event(step_span=step_span, step_outcome=self.outcome)
        else:
            assert isinstance(self.outcome, (Pass, Return, Break, Continue))
            finished = StepCompleted(
                execution_reference=execution_reference,
                processed_natural_program=self.processed_natural_program,
                input_binding_name_to_value=self.input_binding_name_to_value,
                allowed_step_kinds=self.allowed_step_kinds,
                assigned_binding_name_to_value=self.assigned_binding_name_to_value,
                outcome=self.outcome,
            )
            _add_step_completed_event(step_span=step_span, step_outcome_kind=self.outcome.kind)
        return finished, original_exception

    @contextmanager
    def _terminal_boundary(self, finished: StepFinished, original_exception: BaseException | None, step_span: Span) -> Iterator[None]:
        # Notification errors cannot enter classification or cause a second event.
        try:
            yield
        except BaseException as delivery_exception:
            step_span.add_event("nighthawk.step.delivery_failed", {"exception.type": type(delivery_exception).__name__})
            if delivery_exception is original_exception:
                # A host may rethrow its already-recorded exception. Keep its
                # existing cause instead of creating an exception self-cycle.
                raise
            if isinstance(finished, StepInterrupted):
                raise finished.original_exception from delivery_exception
            if original_exception is not None:
                raise delivery_exception from original_exception
            raise
        if isinstance(finished, StepFailed):
            if self.preparation is not None and self.preparation.step_context.operation_failure is finished.original_exception:
                raise finished.original_exception
            raise ExecutionError(finished, description=self.failure_description) from finished.original_exception
        if original_exception is not None:
            raise original_exception

    @contextmanager
    def execution(self, *, caller_frame: FrameType) -> Iterator[Runner]:
        with self._execution_scope(caller_frame) as (execution_reference, step_span, lifecycle):
            original_exception: BaseException | None = None
            try:
                self.failure_stage = "started_delivery"
                if lifecycle is not None and lifecycle.on_step_started is not None:
                    started_result = lifecycle.on_step_started(StepStarted(execution_reference))
                    if inspect.isawaitable(started_result):
                        run_coroutine_synchronously(lambda: await_notification(started_result))
                self.failure_stage = "preparation"
                yield self
            except BaseException as exception:
                original_exception = exception
            finished, original_exception = self._classify_finished(original_exception, execution_reference, step_span)
            with self._terminal_boundary(finished, original_exception, step_span):
                if lifecycle is not None and lifecycle.on_step_finished is not None:
                    terminal_result = lifecycle.on_step_finished(finished)
                    if inspect.isawaitable(terminal_result):
                        run_coroutine_synchronously(
                            lambda: await_notification(
                                terminal_result,
                                interrupted=isinstance(finished, StepInterrupted),
                                timeout=lifecycle.interruption_delivery_timeout,
                            )
                        )

    @asynccontextmanager
    async def execution_async(self, *, caller_frame: FrameType) -> AsyncIterator[Runner]:
        with self._execution_scope(caller_frame) as (execution_reference, step_span, lifecycle):
            original_exception: BaseException | None = None
            try:
                self.failure_stage = "started_delivery"
                if lifecycle is not None and lifecycle.on_step_started is not None:
                    await await_notification(lifecycle.on_step_started(StepStarted(execution_reference)))
                self.failure_stage = "preparation"
                yield self
            except BaseException as exception:
                original_exception = exception
            finished, original_exception = self._classify_finished(original_exception, execution_reference, step_span)
            with self._terminal_boundary(finished, original_exception, step_span):
                if lifecycle is not None and lifecycle.on_step_finished is not None:
                    await await_notification(
                        lifecycle.on_step_finished(finished),
                        interrupted=isinstance(finished, StepInterrupted),
                        timeout=lifecycle.interruption_delivery_timeout,
                    )

    def record_assignment(self, name: str, value: object) -> None:
        """Record only a successfully completed generated Python assignment."""
        self.assigned_binding_name_to_value[name] = value
        if self.preparation is not None:
            self.preparation.step_context.record_assignment(name, value)

    def _parse_and_coerce_return_value(self, value: object, return_annotation: object) -> object:
        self.failure_description = "Return value validation failed"
        adapted = TypeAdapter(return_annotation)
        validated = adapted.validate_python(value)
        self.failure_description = None
        return validated

    def _resolve_annotation(self, annotation: object, caller_frame: FrameType) -> object:
        resolved = resolve_annotation(
            annotation,
            python_globals=caller_frame.f_globals,
            python_locals=dict(caller_frame.f_locals),
        )
        if resolved is MISSING_ANNOTATION:
            return object
        # Build the contract during preparation, before executor admission.
        TypeAdapter(resolved)
        return resolved

    def _prepare_step_execution(
        self,
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        is_in_loop: bool,
        *,
        caller_frame: FrameType,
    ) -> _StepPreparation:
        python_locals = caller_frame.f_locals
        python_globals = caller_frame.f_globals

        processed_without_frontmatter, denied_step_kinds = _split_frontmatter(natural_program)
        processed_without_frontmatter = processed_without_frontmatter.lstrip("\n")

        self.processed_natural_program = processed_without_frontmatter
        allowed_step_kinds = _compute_allowed_step_kinds(is_in_loop, denied_step_kinds)
        self.allowed_step_kinds = allowed_step_kinds

        step_globals = _build_step_globals(python_globals)
        scoped_implicit_reference_name_to_value = _current_implicit_references()
        step_locals = dict(python_locals)
        parent_stack = get_step_context_stack()
        parent_name_to_value = parent_stack[-1].environment.snapshot_working_state() if parent_stack else {}
        cell_scope_stack = get_python_cell_scope_stack()
        python_name_to_cell = cell_scope_stack[-1] if cell_scope_stack else {}

        local_name_set = set(caller_frame.f_code.co_varnames) | set(caller_frame.f_code.co_cellvars)
        cell_name_set = set(caller_frame.f_code.co_freevars)
        module_name_to_annotation = python_globals.get("__annotations__", {})
        for name in output_binding_names:
            annotation = binding_name_to_type.get(name, MISSING_ANNOTATION)
            if name not in local_name_set | cell_name_set:
                if isinstance(module_name_to_annotation, dict) and name in module_name_to_annotation:
                    annotation = module_name_to_annotation[name]
                    if isinstance(annotation, str):
                        annotation = SourceAnnotation((annotation,))
                if name in python_globals:
                    step_locals[name] = python_globals[name]
            if annotation is not MISSING_ANNOTATION:
                annotation = self._resolve_annotation(annotation, caller_frame)
            binding_name_to_type[name] = annotation

        _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)
        environment = StepEnvironment(
            python_name_to_value=python_locals,
            python_globals=python_globals,
            local_name_set=frozenset(local_name_set),
            python_name_to_cell=python_name_to_cell,
            parent_name_to_value=parent_name_to_value,
            task_name_to_value=scoped_implicit_reference_name_to_value,
            output_name_set=frozenset(output_binding_names),
            binding_name_to_type=binding_name_to_type,
        )
        self.input_binding_name_to_value = {name: environment.resolve(name) for name in input_binding_names}

        tool_result_rendering_policy = getattr(self.step_executor, "tool_result_rendering_policy", None)
        if tool_result_rendering_policy is not None and not isinstance(tool_result_rendering_policy, ToolResultRenderingPolicy):
            raise ExecutionError("Step executor tool_result_rendering_policy must be ToolResultRenderingPolicy when provided")

        binding_commit_targets = set(output_binding_names)
        read_binding_names = frozenset(input_binding_names) - binding_commit_targets
        implicit_type_alias_reference_names = _discover_implicit_type_alias_reference_names(
            step_locals=step_locals,
            step_globals=step_globals,
            input_binding_names=input_binding_names,
        )

        implicit_reference_name_to_value: dict[str, object] = dict(scoped_implicit_reference_name_to_value)
        implicit_reference_name_to_value.update(
            {
                implicit_reference_name: step_globals[implicit_reference_name]
                for implicit_reference_name in implicit_type_alias_reference_names
                if implicit_reference_name in step_globals
            }
        )

        step_context = StepContext(
            execution_reference=get_execution_reference(),
            step_globals=step_globals,
            step_locals=step_locals,
            binding_commit_targets=binding_commit_targets,
            read_binding_names=read_binding_names,
            implicit_reference_name_to_value=implicit_reference_name_to_value,
            processed_natural_program=processed_without_frontmatter,
            binding_name_to_type=binding_name_to_type,
            tool_result_rendering_policy=tool_result_rendering_policy,
        )
        step_context.environment = environment

        input_binding_name_to_value = self.input_binding_name_to_value
        return _StepPreparation(
            processed_program=processed_without_frontmatter,
            allowed_step_kinds=allowed_step_kinds,
            step_context=step_context,
            input_binding_name_to_value=input_binding_name_to_value,
            output_binding_names=tuple(output_binding_names),
            binding_name_to_type=step_context.binding_name_to_type,
        )

    def _require_allowed_step_kind(
        self,
        *,
        step_outcome: StepResult,
        allowed_step_kinds: tuple[StepKind, ...],
    ) -> None:
        if step_outcome.kind not in allowed_step_kinds:
            raise ExecutionError(f"Step '{step_outcome.kind}' is not allowed for this step. Allowed kinds: {allowed_step_kinds}")

    def _validate_and_coerce_output_bindings(
        self,
        *,
        preparation: _StepPreparation,
        bindings: dict[str, object],
    ) -> dict[str, object]:
        undeclared = bindings.keys() - set(preparation.output_binding_names)
        if undeclared:
            raise ExecutionError(f"Undeclared output binding names: {sorted(undeclared)}")
        validated_binding_name_to_value = dict(bindings)
        for binding_name in preparation.output_binding_names:
            if binding_name not in validated_binding_name_to_value:
                continue

            if binding_name not in preparation.binding_name_to_type:
                continue
            expected_type = preparation.binding_name_to_type[binding_name]

            self.failure_description = f"Output binding '{binding_name}' failed validation"
            validated_binding_name_to_value[binding_name] = TypeAdapter(expected_type).validate_python(validated_binding_name_to_value[binding_name])
            self.failure_description = None

        return validated_binding_name_to_value

    def _validate_bindings_for_outcome(
        self,
        *,
        preparation: _StepPreparation,
        step_outcome: StepResult,
        bindings: dict[str, object],
    ) -> dict[str, object]:
        undeclared = bindings.keys() - set(preparation.output_binding_names)
        if undeclared:
            raise ExecutionError(f"Undeclared output binding names: {sorted(undeclared)}")
        if step_outcome.kind == "raise":
            return {}
        environment = preparation.step_context.environment
        selected_binding_name_to_value: dict[str, object] = {}
        for name in preparation.output_binding_names:
            if name in environment.step_name_to_value:
                selected_binding_name_to_value[name] = environment.step_name_to_value[name]
                continue
            try:
                selected_binding_name_to_value[name] = environment.python_name_to_value[name]
            except (KeyError, UnboundEnvironmentNameError):
                continue
        selected_binding_name_to_value.update(bindings)
        return self._validate_and_coerce_output_bindings(preparation=preparation, bindings=selected_binding_name_to_value)

    async def _apply_step_oversight_if_needed(
        self,
        *,
        preparation: _StepPreparation,
        outcome: StepResult,
        bindings: dict[str, object],
        return_annotation: object,
    ) -> tuple[StepResult, dict[str, object]]:
        oversight = get_oversight()
        if oversight is None or oversight.inspect_step_commit is None:
            return outcome, bindings
        execution_reference = get_execution_reference()
        step_commit = StepCommit(
            execution_reference=execution_reference,
            processed_natural_program=preparation.processed_program,
            input_binding_name_to_value=preparation.input_binding_name_to_value,
            outcome=outcome,
            binding_name_to_value=bindings,
            allowed_step_kinds=preparation.allowed_step_kinds,
            output_binding_name_set=frozenset(preparation.output_binding_names),
            binding_name_to_type=preparation.binding_name_to_type,
        )
        decision = oversight.inspect_step_commit(step_commit)
        if inspect.isawaitable(decision):
            decision = await decision

        if isinstance(decision, Reject):
            record_oversight_decision(
                subject="step_commit",
                verdict="reject",
                execution_reference=execution_reference,
                reason=decision.reason,
            )
            raise OversightRejectedError(decision.reason, subject="step_commit")

        if isinstance(decision, Accept):
            record_oversight_decision(
                subject="step_commit",
                verdict="accept",
                execution_reference=execution_reference,
                reason=decision.reason,
            )
            return outcome, bindings

        if not isinstance(decision, Rewrite):
            raise NighthawkError("Oversight inspect_step_commit must return Accept, Reject, or Rewrite")

        record_oversight_decision(
            subject="step_commit",
            verdict="rewrite",
            execution_reference=execution_reference,
            reason=decision.reason,
        )

        self.failure_stage = "rewrite_validation"
        next_outcome = outcome if isinstance(decision.outcome, UnsetType) else decision.outcome
        self._require_allowed_step_kind(step_outcome=next_outcome, allowed_step_kinds=preparation.allowed_step_kinds)
        if not isinstance(decision.outcome, UnsetType) and isinstance(next_outcome, Return):
            next_outcome = Return(self._parse_and_coerce_return_value(next_outcome.value, return_annotation))

        next_bindings = bindings
        if isinstance(next_outcome, Raise):
            self._validate_raise(next_outcome)
            if not isinstance(decision.binding_name_to_value, UnsetType) and decision.binding_name_to_value:
                raise NighthawkError("Rewrite Raise cannot include output bindings")
            next_bindings = {}
        elif not isinstance(decision.binding_name_to_value, UnsetType):
            next_bindings = self._validate_and_coerce_output_bindings(preparation=preparation, bindings=dict(decision.binding_name_to_value))
        return next_outcome, next_bindings

    async def _finalize_step(
        self,
        *,
        preparation: _StepPreparation,
        candidate: StepCandidate,
        return_annotation: object,
    ) -> StepEnvelope:
        step_context = preparation.step_context
        if step_context.operation_failure is not None:
            raise step_context.operation_failure
        self.failure_stage = "outcome_validation"
        if not isinstance(candidate, StepCandidate):
            raise ExecutionError("Step executor must return StepCandidate")
        step_outcome = candidate.outcome
        bindings = dict(candidate.binding_name_to_value)
        self.attempted_outcome = step_outcome
        self._require_allowed_step_kind(step_outcome=step_outcome, allowed_step_kinds=preparation.allowed_step_kinds)
        self.failure_stage = "binding_validation"
        validated_bindings = self._validate_bindings_for_outcome(
            preparation=preparation,
            step_outcome=step_outcome,
            bindings=bindings,
        )
        self.validated_binding_name_to_value = validated_bindings
        outcome = step_outcome
        if isinstance(outcome, Return):
            self.failure_stage = "return_validation"
            outcome = Return(self._parse_and_coerce_return_value(outcome.value, return_annotation))
        if isinstance(outcome, Raise):
            self.failure_stage = "outcome_validation"
            self._validate_raise(outcome)
        self.attempted_outcome = outcome
        self.failure_stage = "commit_inspection"
        outcome, validated_bindings = await self._apply_step_oversight_if_needed(
            preparation=preparation,
            outcome=outcome,
            bindings=validated_bindings,
            return_annotation=return_annotation,
        )
        self.outcome = outcome
        self.attempted_outcome = outcome
        self.validated_binding_name_to_value = validated_bindings
        if isinstance(outcome, Raise):
            self._validate_raise(outcome)
            self.failure_stage = "raise_construction"
            self.raise_exception = outcome.error_type(outcome.message)
        self.failure_stage = "binding_assignment"
        return StepEnvelope(outcome=outcome, input_bindings=dict(preparation.input_binding_name_to_value), bindings=validated_bindings)

    def _prepare(
        self,
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        is_in_loop: bool,
        caller_frame: FrameType,
    ) -> _StepPreparation:
        preparation = self._prepare_step_execution(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            is_in_loop,
            caller_frame=caller_frame,
        )
        self.preparation = preparation
        self.context_stack.enter_context(step_context_scope(preparation.step_context))
        self.failure_stage = "executor"
        return preparation

    async def _execute_async(
        self,
        preparation: _StepPreparation,
        output_binding_names: list[str],
        return_annotation: object,
        *,
        allow_awaitable_return: bool,
    ) -> StepEnvelope:
        request = self._build_request(preparation, output_binding_names, return_annotation, allow_awaitable_return)
        executor = self.step_executor
        if isinstance(executor, AsyncStepExecutor):
            candidate = await executor.run_step_async(request=request)
        elif isinstance(executor, SyncStepExecutor):
            candidate = executor.run_step(request=request)
        else:
            raise ExecutionError("Step executor must define run_step_async(...) or run_step(...)")
        return await self._finalize_step(preparation=preparation, candidate=candidate, return_annotation=return_annotation)

    @staticmethod
    def _validate_raise(outcome: Raise) -> None:
        if not isinstance(outcome.error_type, type) or not issubclass(outcome.error_type, BaseException):
            raise ExecutionError("Raise error_type must be a resolved exception class")

    @staticmethod
    def _build_request(
        preparation: _StepPreparation,
        output_binding_names: list[str],
        return_annotation: object,
        allow_awaitable_return: bool,
    ) -> StepRequest:
        return StepRequest(
            execution_reference=preparation.step_context.execution_reference,
            processed_natural_program=preparation.processed_program,
            step_context=preparation.step_context,
            output_binding_names=tuple(output_binding_names),
            binding_name_to_type=preparation.binding_name_to_type,
            return_annotation=return_annotation,
            allowed_step_kinds=preparation.allowed_step_kinds,
            allow_awaitable_return=allow_awaitable_return,
        )

    def run_step(
        self,
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        return_annotation: object,
        is_in_loop: bool,
        *,
        caller_frame: FrameType | None = None,
    ) -> StepEnvelope:
        if caller_frame is None:
            caller_frame = typing.cast(FrameType, inspect.currentframe()).f_back
            assert caller_frame is not None
        preparation = self._prepare(natural_program, input_binding_names, output_binding_names, binding_name_to_type, is_in_loop, caller_frame)
        self.failure_stage = "preparation"
        return_annotation = self._resolve_annotation(return_annotation, caller_frame)
        self.failure_stage = "executor"
        # Keep synchronous executors on the caller thread, even inside a running event loop.
        if isinstance(self.step_executor, SyncStepExecutor):
            request = self._build_request(preparation, output_binding_names, return_annotation, False)
            candidate = self.step_executor.run_step(request=request)
            return run_coroutine_synchronously(
                lambda: self._finalize_step(preparation=preparation, candidate=candidate, return_annotation=return_annotation)
            )
        return run_coroutine_synchronously(
            lambda: self._execute_async(
                preparation,
                output_binding_names,
                return_annotation,
                allow_awaitable_return=False,
            )
        )

    async def run_step_async(
        self,
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        return_annotation: object,
        is_in_loop: bool,
        *,
        caller_frame: FrameType | None = None,
    ) -> StepEnvelope:
        if caller_frame is None:
            caller_frame = typing.cast(FrameType, inspect.currentframe()).f_back
            assert caller_frame is not None
        preparation = self._prepare(natural_program, input_binding_names, output_binding_names, binding_name_to_type, is_in_loop, caller_frame)
        self.failure_stage = "preparation"
        return_annotation = self._resolve_annotation(return_annotation, caller_frame)
        self.failure_stage = "executor"
        return await self._execute_async(preparation, output_binding_names, return_annotation, allow_awaitable_return=True)
