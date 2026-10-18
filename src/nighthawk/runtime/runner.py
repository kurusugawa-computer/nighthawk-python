from __future__ import annotations

import inspect
import typing
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import ExitStack, asynccontextmanager, contextmanager
from types import FrameType
from typing import TypedDict, cast

from opentelemetry.trace import Span, Status, StatusCode
from pydantic import TypeAdapter
from typing_extensions import TypeForm

from ..composition import UnsetType
from ..errors import ExecutionError, NighthawkError
from ..lifecycle import FailureStage, StepCompleted, StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepRaised, StepStarted
from ..oversight import (
    Accept,
    OversightRejectedError,
    Reject,
    Rewrite,
    StepCommit,
    record_oversight_decision,
)
from .async_bridge import run_coroutine_synchronously
from .bindings import BindingValidationError, require_declared_binding_names, validate_output_bindings
from .execution_reference import ExecutionReference
from .interruption import chain_cleanup_errors
from .notifications import await_notification
from .preparation import PreparedStep, StepPreparation
from .scoping import (
    RUN_ID,
    SCOPE_ID,
    SOURCE_LOCATION,
    STEP_EXECUTION_ID,
    get_execution_reference,
    get_lifecycle,
    get_oversight,
    span,
    step_execution_reference_scope,
)
from .step_context import (
    step_context_scope,
)
from .step_contract import StepKind
from .step_executor import AsyncStepExecutor, StepExecutor, SyncStepExecutor
from .step_request import StepCandidate, StepRequest, _snapshot_output_bindings
from .step_result import Break, Continue, Pass, Raise, Return, StepResult


class StepEnvelope(TypedDict):
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


def _build_source_location(*, caller_frame: FrameType) -> str:
    module_name = caller_frame.f_globals.get("__name__")
    if not isinstance(module_name, str) or not module_name:
        module_name = "<unknown_module>"
    return f"{module_name}:{caller_frame.f_lineno}"


class Runner:
    def __init__(self, step_executor: StepExecutor, *, runtime_name_set: frozenset[str] = frozenset()) -> None:
        self.step_executor = step_executor
        self.preparation_state = StepPreparation(step_executor, runtime_name_set)
        self.failure_stage: FailureStage = "preparation"
        self.failure_description: str | None = None
        self.preparation: PreparedStep | None = None
        self.validated_binding_name_to_value: dict[str, object] | None = None
        self.assigned_binding_name_to_value: dict[str, object] = {}
        self.attempted_outcome: StepResult | None = None
        self.outcome: StepResult | None = None
        self.raise_exception: BaseException | None = None
        self.context_stack = ExitStack()

    @contextmanager
    def _execution_scope(self, source_location: str) -> Iterator[tuple[ExecutionReference, Span, StepLifecycle | None]]:
        with step_execution_reference_scope(source_location=source_location) as execution_reference:
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
                processed_natural_program=self.preparation_state.processed_natural_program,
                input_binding_name_to_value=self.preparation_state.input_binding_name_to_value,
                allowed_step_kinds=self.preparation_state.allowed_step_kinds,
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
                processed_natural_program=self.preparation_state.processed_natural_program,
                input_binding_name_to_value=self.preparation_state.input_binding_name_to_value,
                allowed_step_kinds=self.preparation_state.allowed_step_kinds,
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
                processed_natural_program=self.preparation_state.processed_natural_program,
                input_binding_name_to_value=self.preparation_state.input_binding_name_to_value,
                allowed_step_kinds=self.preparation_state.allowed_step_kinds,
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
                processed_natural_program=self.preparation_state.processed_natural_program,
                input_binding_name_to_value=self.preparation_state.input_binding_name_to_value,
                allowed_step_kinds=self.preparation_state.allowed_step_kinds,
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
                chain_cleanup_errors(finished.original_exception, delivery_exception)
                raise finished.original_exception from finished.original_exception.__cause__
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
    def execution(self, *, caller_frame: FrameType | None = None, source_location: str = "<embedded>:0") -> Iterator[Runner]:
        if caller_frame is not None:
            source_location = _build_source_location(caller_frame=caller_frame)
        with self._execution_scope(source_location) as (execution_reference, step_span, lifecycle):
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
    async def execution_async(self, *, caller_frame: FrameType | None = None, source_location: str = "<embedded>:0") -> AsyncIterator[Runner]:
        if caller_frame is not None:
            source_location = _build_source_location(caller_frame=caller_frame)
        with self._execution_scope(source_location) as (execution_reference, step_span, lifecycle):
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
        adapted = TypeAdapter(cast(TypeForm[object], return_annotation))
        validated = adapted.validate_python(value)
        self.failure_description = None
        return validated

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
        preparation: PreparedStep,
        bindings: dict[str, object],
    ) -> dict[str, object]:
        self.failure_description = None
        try:
            return validate_output_bindings(bindings, preparation.binding_name_to_type, preparation.output_binding_names)
        except BindingValidationError as exception:
            self.failure_description = f"Output binding '{exception.binding_name}' failed validation"
            # Preserve the ordinary lifecycle's original validator exception.
            raise exception.original_exception from None

    def _validate_bindings_for_outcome(
        self,
        *,
        preparation: PreparedStep,
        step_outcome: StepResult,
        bindings: Mapping[str, object] | UnsetType,
    ) -> dict[str, object]:
        if not isinstance(bindings, UnsetType):
            require_declared_binding_names(bindings, preparation.output_binding_names)
        if step_outcome.kind == "raise":
            if not isinstance(bindings, UnsetType) and len(bindings):
                raise NighthawkError("StepCandidate Raise cannot include output bindings")
            return {}
        selected_binding_name_to_value = (
            _snapshot_output_bindings(preparation.step_context.environment, preparation.output_binding_names)
            if isinstance(bindings, UnsetType)
            else dict(bindings)
        )
        return self._validate_and_coerce_output_bindings(preparation=preparation, bindings=selected_binding_name_to_value)

    async def _apply_step_oversight_if_needed(
        self,
        *,
        preparation: PreparedStep,
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
        preparation: PreparedStep,
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
        self.attempted_outcome = step_outcome
        self._require_allowed_step_kind(step_outcome=step_outcome, allowed_step_kinds=preparation.allowed_step_kinds)
        self.failure_stage = "binding_validation"
        validated_bindings = self._validate_bindings_for_outcome(
            preparation=preparation,
            step_outcome=step_outcome,
            bindings=candidate.binding_name_to_value,
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
        caller_frame: FrameType | None,
        name_to_value: Mapping[str, object] | None,
    ) -> PreparedStep:
        preparation = self.preparation_state.prepare(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            is_in_loop,
            caller_frame=caller_frame,
            name_to_value=name_to_value,
        )
        self.preparation = preparation
        self.context_stack.enter_context(step_context_scope(preparation.step_context))
        self.failure_stage = "executor"
        return preparation

    async def _execute_async(
        self,
        preparation: PreparedStep,
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
        preparation: PreparedStep,
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
        name_to_value: Mapping[str, object] | None = None,
    ) -> StepEnvelope:
        if caller_frame is None and name_to_value is None:
            caller_frame = typing.cast(FrameType, inspect.currentframe()).f_back
            assert caller_frame is not None
        preparation = self._prepare(
            natural_program, input_binding_names, output_binding_names, binding_name_to_type, is_in_loop, caller_frame, name_to_value
        )
        self.failure_stage = "preparation"
        return_annotation = self.preparation_state.resolve_step_annotation(
            return_annotation, caller_frame=caller_frame, environment=preparation.step_context.environment
        )
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
        name_to_value: Mapping[str, object] | None = None,
    ) -> StepEnvelope:
        if caller_frame is None and name_to_value is None:
            caller_frame = typing.cast(FrameType, inspect.currentframe()).f_back
            assert caller_frame is not None
        preparation = self._prepare(
            natural_program, input_binding_names, output_binding_names, binding_name_to_type, is_in_loop, caller_frame, name_to_value
        )
        self.failure_stage = "preparation"
        return_annotation = self.preparation_state.resolve_step_annotation(
            return_annotation, caller_frame=caller_frame, environment=preparation.step_context.environment
        )
        self.failure_stage = "executor"
        return await self._execute_async(preparation, output_binding_names, return_annotation, allow_awaitable_return=True)
