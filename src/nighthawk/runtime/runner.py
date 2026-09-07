from __future__ import annotations

import ast
import functools
import inspect
import typing
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from types import FrameType
from typing import TypeAliasType, TypedDict, cast

from opentelemetry.trace import Span, Status, StatusCode
from pydantic import TypeAdapter

from ..composition import UNSET, UnsetType
from ..errors import ExecutionError, NaturalParseError, NighthawkError
from ..lifecycle import FailureStage, StepCompleted, StepFailed, StepFinished, StepInterrupted, StepRaised
from ..natural.blocks import parse_frontmatter, validate_frontmatter_deny
from ..oversight import (
    Accept,
    OversightRejectedError,
    Reject,
    ReturnExpression,
    Rewrite,
    StepCommit,
    record_oversight_decision,
)
from .async_bridge import run_coroutine_synchronously
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
    _MISSING,
    StepContext,
    ToolResultRenderingPolicy,
    get_python_cell_scope_stack,
    get_python_name_scope_stack,
    get_step_context_stack,
    resolve_name_in_step_context,
    step_context_scope,
)
from .step_contract import (
    RaiseStepOutcome,
    ReturnStepOutcome,
    StepKind,
    StepOutcome,
)
from .step_executor import AsyncStepExecutor, StepExecutor, SyncStepExecutor
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
        if declared_type is not object:
            continue
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


def _build_step_locals(
    python_locals: dict[str, object],
) -> dict[str, object]:
    step_locals: dict[str, object] = {}
    step_context_stack = get_step_context_stack()
    if step_context_stack:
        step_locals.update(step_context_stack[-1].step_locals)
    step_locals.update(python_locals)
    return step_locals


def _resolve_input_bindings(
    input_binding_names: list[str],
    *,
    python_locals: dict[str, object],
    python_globals: dict[str, object],
    caller_frame: FrameType,
) -> dict[str, tuple[object, str]]:
    """Resolve each input binding using Python LEGB rules.

    Returns a mapping of binding name to (resolved_value, resolution_kind).
    """
    local_variable_name_set = set(caller_frame.f_code.co_varnames)
    local_variable_name_set.update(caller_frame.f_code.co_cellvars)
    free_variable_name_set = set(caller_frame.f_code.co_freevars)

    python_cell_scope_stack = get_python_cell_scope_stack()
    python_name_scope_stack = get_python_name_scope_stack()

    python_builtins = python_globals.get("__builtins__", __builtins__)

    def resolve_one(binding_name: str) -> tuple[object, str]:
        if binding_name in python_locals:
            return python_locals[binding_name], "locals"

        for scope in reversed(python_cell_scope_stack):
            if binding_name not in scope:
                continue
            cell = scope[binding_name]
            try:
                return cell.cell_contents, "cell_scope"
            except ValueError:
                break

        if binding_name in local_variable_name_set:
            raise UnboundLocalError(f"cannot access local variable {binding_name!r} where it is not associated with a value")

        if binding_name in free_variable_name_set:
            error = NameError(f"cannot access free variable {binding_name!r} where it is not associated with a value in enclosing scope")
            error.name = binding_name
            raise error

        for scope in reversed(python_name_scope_stack):
            if binding_name in scope:
                return scope[binding_name], "name_scope"

        if binding_name in python_globals:
            return python_globals[binding_name], "globals"

        if isinstance(python_builtins, dict) and binding_name in python_builtins:
            return python_builtins[binding_name], "builtins"

        if hasattr(python_builtins, binding_name):
            return getattr(python_builtins, binding_name), "builtins"

        error = NameError(f"name {binding_name!r} is not defined")
        error.name = binding_name
        raise error

    binding_name_to_value_and_resolution_kind: dict[str, tuple[object, str]] = {}
    for binding_name in input_binding_names:
        binding_name_to_value_and_resolution_kind[binding_name] = resolve_one(binding_name)
    return binding_name_to_value_and_resolution_kind


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
    if step_outcome.error_type is not None:
        event_attributes["nighthawk.step.raise_error_type"] = step_outcome.error_type
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
        self.attempted_outcome: StepOutcome | StepResult | None = None
        self.outcome: StepResult | None = None
        self.raise_exception: BaseException | None = None
        self.context_stack = ExitStack()

    @contextmanager
    def execution(self, *, caller_frame: FrameType) -> Iterator[Runner]:
        """Keep identity and diagnostics active through assignments and delivery."""
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
                original_exception: BaseException | None = None
                try:
                    yield self
                except BaseException as exception:
                    original_exception = exception
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
                        inspection_subject=cast(typing.Literal["return_expression", "step_commit", "tool_call"], rejection.subject)
                        if rejection
                        else None,
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
                # Delivery is outside classification: no callback failure can reenter it.
                try:
                    if lifecycle is not None and lifecycle.on_step_finished is not None:
                        lifecycle.on_step_finished(finished)
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
                    raise ExecutionError(finished, description=self.failure_description) from finished.original_exception
                if original_exception is not None:
                    raise original_exception

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
        step_globals.update(scoped_implicit_reference_name_to_value)
        step_locals = _build_step_locals(python_locals)

        resolved_bindings = _resolve_input_bindings(
            input_binding_names,
            python_locals=python_locals,
            python_globals=python_globals,
            caller_frame=caller_frame,
        )

        self.input_binding_name_to_value = {name: value for name, (value, _) in resolved_bindings.items()}

        for binding_name, (value, resolution_kind) in resolved_bindings.items():
            if resolution_kind in ("locals", "cell_scope", "name_scope"):
                step_locals[binding_name] = value

        tool_result_rendering_policy = getattr(self.step_executor, "tool_result_rendering_policy", None)
        if tool_result_rendering_policy is not None and not isinstance(tool_result_rendering_policy, ToolResultRenderingPolicy):
            raise ExecutionError("Step executor tool_result_rendering_policy must be ToolResultRenderingPolicy when provided")

        _infer_binding_types_from_initial_values(binding_name_to_type, step_locals)

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

        input_binding_name_to_value = {name: value for name, (value, _) in resolved_bindings.items()}
        return _StepPreparation(
            processed_program=processed_without_frontmatter,
            allowed_step_kinds=allowed_step_kinds,
            step_context=step_context,
            input_binding_name_to_value=input_binding_name_to_value,
        )

    def _resolve_raise_error_type(
        self,
        step_context: StepContext,
        step_outcome: Raise,
    ) -> type[BaseException]:
        if step_outcome.error_type is not None:
            resolved_raise_error_type = resolve_name_in_step_context(step_context, step_outcome.error_type)
            if resolved_raise_error_type is _MISSING:
                raise ExecutionError(f"Invalid raise_error_type: {step_outcome.error_type!r}: {step_outcome.message}")
            if not isinstance(resolved_raise_error_type, type) or not issubclass(resolved_raise_error_type, BaseException):
                raise ExecutionError(f"Invalid raise_error_type: {step_outcome.error_type!r}: {step_outcome.message}")
            return resolved_raise_error_type

        return ExecutionError

    def _require_allowed_step_kind(
        self,
        *,
        step_outcome: StepOutcome | StepResult,
        allowed_step_kinds: tuple[StepKind, ...],
    ) -> None:
        if step_outcome.kind not in allowed_step_kinds:
            raise ExecutionError(f"Step '{step_outcome.kind}' is not allowed for this step. Allowed kinds: {allowed_step_kinds}")

    def _validate_and_coerce_output_bindings(
        self,
        *,
        step_context: StepContext,
        bindings: dict[str, object],
    ) -> dict[str, object]:
        undeclared = bindings.keys() - step_context.binding_commit_targets
        if undeclared:
            raise ExecutionError(f"Undeclared output binding names: {sorted(undeclared)}")
        validated_binding_name_to_value = dict(bindings)
        for binding_name in step_context.binding_commit_targets:
            if binding_name not in validated_binding_name_to_value:
                continue

            expected_type = step_context.binding_name_to_type.get(binding_name)
            if expected_type is None:
                continue

            self.failure_description = f"Output binding '{binding_name}' failed validation"
            validated_binding_name_to_value[binding_name] = TypeAdapter(expected_type).validate_python(validated_binding_name_to_value[binding_name])
            self.failure_description = None

        return validated_binding_name_to_value

    def _validate_bindings_for_outcome(
        self,
        *,
        step_context: StepContext,
        step_outcome: StepOutcome,
        bindings: dict[str, object],
    ) -> dict[str, object]:
        if step_outcome.kind == "raise":
            return {}
        return self._validate_and_coerce_output_bindings(step_context=step_context, bindings=bindings)

    async def _resolve_return_value(
        self,
        *,
        step_context: StepContext,
        step_outcome: StepOutcome,
        bindings: dict[str, object],
        return_annotation: object,
        allow_awaitable_return: bool,
    ) -> object | None:
        if not isinstance(step_outcome, ReturnStepOutcome):
            return None

        self.failure_stage = "return_inspection"
        oversight = get_oversight()
        if oversight is not None and oversight.inspect_return_expression is not None:
            request = ReturnExpression(
                execution_reference=get_execution_reference(),
                expression=step_outcome.return_expression,
                expected_type=return_annotation,
                processed_natural_program=step_context.processed_natural_program,
                validated_binding_name_to_value=bindings,
            )
            decision = oversight.inspect_return_expression(request)
            if not isinstance(decision, (Accept, Reject)):
                raise NighthawkError("Oversight inspect_return_expression must return Accept or Reject")
            record_oversight_decision(
                subject="return_expression",
                verdict="reject" if isinstance(decision, Reject) else "accept",
                execution_reference=request.execution_reference,
                reason=decision.reason,
            )
            if isinstance(decision, Reject):
                raise OversightRejectedError(decision.reason, subject="return_expression")
        self.failure_stage = "return_evaluation"
        evaluation_locals = {**step_context.step_locals, **bindings}
        self.failure_description = f"Failed to evaluate return_expression {step_outcome.return_expression!r}"
        compiled = compile(step_outcome.return_expression, "<nighthawk-return>", "eval", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        resolved = eval(compiled, step_context.step_globals, evaluation_locals)
        self.failure_description = None
        if inspect.isawaitable(resolved):
            self.failure_stage = "return_await"
            if not allow_awaitable_return:
                if inspect.iscoroutine(resolved):
                    resolved.close()
                raise ExecutionError("Sync Natural function cannot return an awaitable value. Use async def and await the function call.")
            resolved = await resolved
        self.failure_stage = "return_validation"
        return self._parse_and_coerce_return_value(resolved, return_annotation)

    def _apply_step_oversight_if_needed(
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
            output_binding_name_set=frozenset(preparation.step_context.binding_commit_targets),
            binding_name_to_type=preparation.step_context.binding_name_to_type,
        )
        decision = oversight.inspect_step_commit(step_commit)

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
        if decision.return_value is not UNSET:
            if not isinstance(outcome, Return):
                raise NighthawkError("Rewrite return_value requires an existing Return outcome")
            next_outcome = Return(self._parse_and_coerce_return_value(decision.return_value, return_annotation))
        elif not isinstance(decision.outcome, UnsetType) and isinstance(next_outcome, Return):
            next_outcome = Return(self._parse_and_coerce_return_value(next_outcome.value, return_annotation))

        next_bindings = bindings
        if isinstance(next_outcome, Raise):
            if not isinstance(decision.binding_name_to_value, UnsetType) and decision.binding_name_to_value:
                raise NighthawkError("Rewrite Raise cannot include output bindings")
            next_bindings = {}
        elif not isinstance(decision.binding_name_to_value, UnsetType):
            next_bindings = self._validate_and_coerce_output_bindings(
                step_context=preparation.step_context, bindings=dict(decision.binding_name_to_value)
            )
        return next_outcome, next_bindings

    async def _finalize_step(
        self,
        *,
        preparation: _StepPreparation,
        step_outcome: StepOutcome,
        bindings: dict[str, object],
        return_annotation: object,
        allow_awaitable_return: bool,
    ) -> StepEnvelope:
        step_context = preparation.step_context
        self.attempted_outcome = step_outcome
        self.failure_stage = "outcome_validation"
        self._require_allowed_step_kind(step_outcome=step_outcome, allowed_step_kinds=preparation.allowed_step_kinds)
        self.failure_stage = "binding_validation"
        validated_bindings = self._validate_bindings_for_outcome(
            step_context=step_context,
            step_outcome=step_outcome,
            bindings=bindings,
        )
        self.validated_binding_name_to_value = validated_bindings
        return_value = await self._resolve_return_value(
            step_context=step_context,
            step_outcome=step_outcome,
            bindings=validated_bindings,
            return_annotation=return_annotation,
            allow_awaitable_return=allow_awaitable_return,
        )
        if isinstance(step_outcome, ReturnStepOutcome):
            outcome: StepResult = Return(return_value)
        elif isinstance(step_outcome, RaiseStepOutcome):
            outcome = Raise(step_outcome.raise_message, step_outcome.raise_error_type)
        else:
            outcome = {"pass": Pass, "break": Break, "continue": Continue}[step_outcome.kind]()
        self.attempted_outcome = outcome
        initial_outcome = outcome
        resolved_exception_type: type[BaseException] | None = None
        if isinstance(outcome, Raise):
            self.failure_stage = "raise_resolution"
            resolved_exception_type = self._resolve_raise_error_type(step_context, outcome)
        self.failure_stage = "commit_inspection"
        outcome, validated_bindings = self._apply_step_oversight_if_needed(
            preparation=preparation,
            outcome=outcome,
            bindings=validated_bindings,
            return_annotation=return_annotation,
        )
        self.outcome = outcome
        self.attempted_outcome = outcome
        self.validated_binding_name_to_value = validated_bindings
        if isinstance(outcome, Raise):
            self.failure_stage = "raise_resolution"
            exception_type = resolved_exception_type if outcome is initial_outcome else self._resolve_raise_error_type(step_context, outcome)
            assert exception_type is not None
            self.failure_stage = "raise_construction"
            message = outcome.message if outcome.error_type is not None else f"Execution failed: {outcome.message}"
            self.raise_exception = exception_type(message)
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
        executor = self.step_executor
        if isinstance(executor, AsyncStepExecutor):
            outcome, bindings = await executor.run_step_async(
                processed_natural_program=preparation.processed_program,
                step_context=preparation.step_context,
                binding_names=output_binding_names,
                allowed_step_kinds=preparation.allowed_step_kinds,
            )
        elif isinstance(executor, SyncStepExecutor):
            outcome, bindings = executor.run_step(
                processed_natural_program=preparation.processed_program,
                step_context=preparation.step_context,
                binding_names=output_binding_names,
                allowed_step_kinds=preparation.allowed_step_kinds,
            )
        else:
            raise ExecutionError("Step executor must define run_step_async(...) or run_step(...)")
        return await self._finalize_step(
            preparation=preparation,
            step_outcome=outcome,
            bindings=bindings,
            return_annotation=return_annotation,
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
        # Keep synchronous executors on the caller thread, even inside a running event loop.
        if isinstance(self.step_executor, SyncStepExecutor):
            outcome, bindings = self.step_executor.run_step(
                processed_natural_program=preparation.processed_program,
                step_context=preparation.step_context,
                binding_names=output_binding_names,
                allowed_step_kinds=preparation.allowed_step_kinds,
            )
            return run_coroutine_synchronously(
                lambda: self._finalize_step(
                    preparation=preparation,
                    step_outcome=outcome,
                    bindings=bindings,
                    return_annotation=return_annotation,
                    allow_awaitable_return=False,
                )
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
        return await self._execute_async(preparation, output_binding_names, return_annotation, allow_awaitable_return=True)
