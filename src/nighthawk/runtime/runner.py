from __future__ import annotations

import ast
import functools
import inspect
import typing
from collections.abc import Iterable
from dataclasses import dataclass
from types import FrameType
from typing import TypeAliasType, TypedDict

from opentelemetry.trace import Span, Status, StatusCode
from pydantic import TypeAdapter

from ..errors import ExecutionError, NaturalParseError, NighthawkError
from ..natural.blocks import parse_frontmatter, validate_frontmatter_deny
from .async_bridge import run_coroutine_synchronously
from .scoping import RUN_ID, SCOPE_ID, STEP_ID, get_execution_context, get_implicit_reference_name_to_value, span
from .step_context import (
    _MISSING,
    StepContext,
    ToolResultRenderingPolicy,
    get_python_cell_scope_stack,
    get_python_name_scope_stack,
    get_step_context_stack,
    resolve_name_in_step_context,
)
from .step_contract import (
    RaiseStepOutcome,
    ReturnStepOutcome,
    StepOutcome,
)
from .step_executor import AsyncStepExecutor, StepExecutor, SyncStepExecutor


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


def _compute_allowed_step_kinds(is_in_loop: bool, denied_step_kinds: tuple[str, ...]) -> tuple[str, ...]:
    base_allowed_kinds: list[str] = ["pass", "return", "raise"]
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

    step_outcome: StepOutcome
    input_bindings: dict[str, object]
    bindings: dict[str, object]
    return_value: object | None


def _add_step_completed_event(*, step_span: Span, step_outcome_kind: str) -> None:
    step_span.add_event(
        "nighthawk.step.completed",
        {
            "nighthawk.step.outcome_kind": step_outcome_kind,
        },
    )


def _add_step_raised_event(*, step_span: Span, step_outcome: RaiseStepOutcome) -> None:
    event_attributes: dict[str, str] = {
        "nighthawk.step.outcome_kind": step_outcome.kind,
        "nighthawk.step.raise_message": step_outcome.raise_message,
    }
    if step_outcome.raise_error_type is not None:
        event_attributes["nighthawk.step.raise_error_type"] = step_outcome.raise_error_type
    step_span.add_event("nighthawk.step.raised", event_attributes)


def _record_internal_step_failure(*, step_span: Span, exception: NighthawkError) -> None:
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
    allowed_step_kinds: tuple[str, ...]
    step_context: StepContext
    input_binding_name_to_value: dict[str, object]


def _build_step_id(*, caller_frame: FrameType) -> str:
    module_name = caller_frame.f_globals.get("__name__")
    if not isinstance(module_name, str) or not module_name:
        module_name = "<unknown_module>"
    return f"{module_name}:{caller_frame.f_lineno}"


class Runner:
    def __init__(self, step_executor: StepExecutor) -> None:
        self.step_executor = step_executor

    def _parse_and_coerce_return_value(self, value: object, return_annotation: object) -> object:
        try:
            adapted = TypeAdapter(return_annotation)
            return adapted.validate_python(value)
        except Exception as e:
            raise ExecutionError(f"Return value validation failed: {e}") from e

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

        allowed_step_kinds = _compute_allowed_step_kinds(is_in_loop, denied_step_kinds)

        step_globals = _build_step_globals(python_globals)
        scoped_implicit_reference_name_to_value = get_implicit_reference_name_to_value()
        step_globals.update(scoped_implicit_reference_name_to_value)
        step_locals = _build_step_locals(python_locals)

        resolved_bindings = _resolve_input_bindings(
            input_binding_names,
            python_locals=python_locals,
            python_globals=python_globals,
            caller_frame=caller_frame,
        )

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
            step_id=_build_step_id(caller_frame=caller_frame),
            step_globals=step_globals,
            step_locals=step_locals,
            binding_commit_targets=binding_commit_targets,
            read_binding_names=read_binding_names,
            implicit_reference_name_to_value=implicit_reference_name_to_value,
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

    def _build_raise_exception(
        self,
        step_context: StepContext,
        step_outcome: RaiseStepOutcome,
    ) -> BaseException:
        if step_outcome.raise_error_type is not None:
            resolved_raise_error_type = resolve_name_in_step_context(step_context, step_outcome.raise_error_type)
            if resolved_raise_error_type is _MISSING:
                raise ExecutionError(f"Invalid raise_error_type: {step_outcome.raise_error_type!r}: {step_outcome.raise_message}")
            if not isinstance(resolved_raise_error_type, type) or not issubclass(resolved_raise_error_type, BaseException):
                raise ExecutionError(f"Invalid raise_error_type: {step_outcome.raise_error_type!r}: {step_outcome.raise_message}")
            return resolved_raise_error_type(step_outcome.raise_message)

        return ExecutionError(f"Execution failed: {step_outcome.raise_message}")

    def _apply_bindings_and_validate_kind(
        self,
        *,
        step_context: StepContext,
        step_outcome: StepOutcome,
        bindings: dict[str, object],
        allowed_step_kinds: tuple[str, ...],
    ) -> str:
        step_outcome_kind = step_outcome.kind
        if step_outcome_kind not in allowed_step_kinds:
            raise ExecutionError(f"Step '{step_outcome_kind}' is not allowed for this step. Allowed kinds: {allowed_step_kinds}")
        step_context.step_locals.update(bindings)
        return step_outcome_kind

    async def _finalize_step(
        self,
        *,
        preparation: _StepPreparation,
        step_outcome: StepOutcome,
        bindings: dict[str, object],
        return_annotation: object,
        step_span: Span,
        allow_awaitable_return: bool,
    ) -> StepEnvelope:
        try:
            step_outcome_kind = self._apply_bindings_and_validate_kind(
                step_context=preparation.step_context,
                step_outcome=step_outcome,
                bindings=bindings,
                allowed_step_kinds=preparation.allowed_step_kinds,
            )
        except NighthawkError as exception:
            _record_internal_step_failure(step_span=step_span, exception=exception)
            raise

        return_value: object | None = None
        try:
            if step_outcome_kind == "raise":
                assert isinstance(step_outcome, RaiseStepOutcome)
                raise_exception = self._build_raise_exception(preparation.step_context, step_outcome)
                _add_step_raised_event(step_span=step_span, step_outcome=step_outcome)
                raise raise_exception

            if step_outcome_kind == "return":
                assert isinstance(step_outcome, ReturnStepOutcome)
                try:
                    compiled = compile(step_outcome.return_expression, "<nighthawk-return>", "eval", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                    resolved = eval(compiled, preparation.step_context.step_globals, preparation.step_context.step_locals)
                except Exception as e:
                    raise ExecutionError(f"Failed to evaluate return_expression {step_outcome.return_expression!r}: {e}") from e
                if inspect.isawaitable(resolved):
                    if not allow_awaitable_return:
                        raise ExecutionError("Sync Natural function cannot return an awaitable value. Use async def and await the function call.")
                    resolved = await resolved
                return_value = self._parse_and_coerce_return_value(resolved, return_annotation)
        except NighthawkError as exception:
            _record_internal_step_failure(step_span=step_span, exception=exception)
            raise

        _add_step_completed_event(step_span=step_span, step_outcome_kind=step_outcome_kind)
        return StepEnvelope(
            step_outcome=step_outcome,
            input_bindings=dict(preparation.input_binding_name_to_value),
            bindings=bindings,
            return_value=return_value,
        )

    async def _run_step_async_impl(
        self,
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        return_annotation: object,
        is_in_loop: bool,
        *,
        caller_frame: FrameType,
    ) -> StepEnvelope:
        preparation = self._prepare_step_execution(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            is_in_loop,
            caller_frame=caller_frame,
        )
        execution_context = get_execution_context()

        with span(
            "nighthawk.step",
            **{
                RUN_ID: execution_context.run_id,
                SCOPE_ID: execution_context.scope_id,
                STEP_ID: preparation.step_context.step_id,
            },
        ) as step_span:
            step_executor = self.step_executor

            try:
                if isinstance(step_executor, AsyncStepExecutor):
                    step_outcome, bindings = await step_executor.run_step_async(
                        processed_natural_program=preparation.processed_program,
                        step_context=preparation.step_context,
                        binding_names=output_binding_names,
                        allowed_step_kinds=preparation.allowed_step_kinds,
                    )
                elif isinstance(step_executor, SyncStepExecutor):
                    step_outcome, bindings = step_executor.run_step(
                        processed_natural_program=preparation.processed_program,
                        step_context=preparation.step_context,
                        binding_names=output_binding_names,
                        allowed_step_kinds=preparation.allowed_step_kinds,
                    )
                else:
                    raise ExecutionError("Step executor must define run_step_async(...) or run_step(...)")
            except NighthawkError as exception:
                _record_internal_step_failure(step_span=step_span, exception=exception)
                raise

            return await self._finalize_step(
                preparation=preparation,
                step_outcome=step_outcome,
                bindings=bindings,
                return_annotation=return_annotation,
                step_span=step_span,
                allow_awaitable_return=True,
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
        caller_frame: FrameType,
    ) -> StepEnvelope:
        preparation = self._prepare_step_execution(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            is_in_loop,
            caller_frame=caller_frame,
        )

        if isinstance(self.step_executor, SyncStepExecutor):
            execution_context = get_execution_context()
            with span(
                "nighthawk.step",
                **{
                    RUN_ID: execution_context.run_id,
                    SCOPE_ID: execution_context.scope_id,
                    STEP_ID: preparation.step_context.step_id,
                },
            ) as step_span:
                try:
                    step_outcome, bindings = self.step_executor.run_step(
                        processed_natural_program=preparation.processed_program,
                        step_context=preparation.step_context,
                        binding_names=output_binding_names,
                        allowed_step_kinds=preparation.allowed_step_kinds,
                    )
                except NighthawkError as exception:
                    _record_internal_step_failure(step_span=step_span, exception=exception)
                    raise

                return run_coroutine_synchronously(
                    lambda: self._finalize_step(
                        preparation=preparation,
                        step_outcome=step_outcome,
                        bindings=bindings,
                        return_annotation=return_annotation,
                        step_span=step_span,
                        allow_awaitable_return=False,
                    )
                )

        return run_coroutine_synchronously(
            lambda: self._run_step_async_impl(
                natural_program,
                input_binding_names,
                output_binding_names,
                binding_name_to_type,
                return_annotation,
                is_in_loop,
                caller_frame=caller_frame,
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
        caller_frame: FrameType,
    ) -> StepEnvelope:
        return await self._run_step_async_impl(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            return_annotation,
            is_in_loop,
            caller_frame=caller_frame,
        )
