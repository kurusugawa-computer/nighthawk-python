from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from types import FrameType
from typing import TypedDict

from pydantic import TypeAdapter

from ..errors import ExecutionError, NaturalParseError
from ..identifier_path import parse_identifier_path
from ..natural.blocks import parse_frontmatter, validate_frontmatter_deny
from .async_bridge import run_coroutine_synchronously
from .scoping import RUN_ID, SCOPE_ID, STEP_ID, get_execution_context, span
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


@dataclass(frozen=True)
class _StepPreparation:
    """Result of preparing a Natural block for execution."""

    processed_program: str
    allowed_step_kinds: tuple[str, ...]
    step_context: StepContext
    input_binding_name_to_value: dict[str, object]


class Runner:
    def __init__(self, step_executor: StepExecutor) -> None:
        self.step_executor = step_executor

    def _parse_and_coerce_return_value(self, value: object, return_annotation: object) -> object:
        try:
            adapted = TypeAdapter(return_annotation)
            return adapted.validate_python(value)
        except Exception as e:
            raise ExecutionError(f"Return value validation failed: {e}") from e

    def _resolve_reference_path(self, step_context: StepContext, return_reference_path: str) -> object:
        parsed_path = parse_identifier_path(return_reference_path)
        if parsed_path is None:
            raise ExecutionError(f"Invalid return_reference_path: {return_reference_path!r}")

        root_name = parsed_path[0]
        if root_name not in step_context.step_locals:
            raise ExecutionError(f"Unknown root name in return_reference_path: {root_name}")
        current = step_context.step_locals[root_name]

        for part in parsed_path[1:]:
            try:
                current = getattr(current, part)
            except Exception as e:
                raise ExecutionError(f"Failed to resolve return_reference_path segment {part!r} in {return_reference_path!r}") from e

        return current

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

        binding_commit_targets = set(output_binding_names)
        read_binding_names = frozenset(input_binding_names) - binding_commit_targets

        step_context = StepContext(
            step_id=str(uuid.uuid4()),
            step_globals=step_globals,
            step_locals=step_locals,
            binding_commit_targets=binding_commit_targets,
            read_binding_names=read_binding_names,
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

    def _validate_raise_outcome(
        self,
        step_context: StepContext,
        step_outcome: RaiseStepOutcome,
    ) -> None:
        if step_outcome.raise_error_type is not None:
            resolved_raise_error_type = resolve_name_in_step_context(step_context, step_outcome.raise_error_type)
            if resolved_raise_error_type is _MISSING:
                raise ExecutionError(f"Invalid raise_error_type: {step_outcome.raise_error_type!r}: {step_outcome.raise_message}")
            if not isinstance(resolved_raise_error_type, type) or not issubclass(resolved_raise_error_type, BaseException):
                raise ExecutionError(f"Invalid raise_error_type: {step_outcome.raise_error_type!r}: {step_outcome.raise_message}")
            raise resolved_raise_error_type(step_outcome.raise_message)

        raise ExecutionError(f"Execution failed: {step_outcome.raise_message}")

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

    def _finalize_step(
        self,
        *,
        preparation: _StepPreparation,
        step_outcome: StepOutcome,
        bindings: dict[str, object],
        return_annotation: object,
    ) -> StepEnvelope:
        """Sync finalization: validate kind, resolve return value, handle raise."""
        step_outcome_kind = self._apply_bindings_and_validate_kind(
            step_context=preparation.step_context,
            step_outcome=step_outcome,
            bindings=bindings,
            allowed_step_kinds=preparation.allowed_step_kinds,
        )

        return_value: object | None = None
        if step_outcome_kind == "return":
            assert isinstance(step_outcome, ReturnStepOutcome)
            resolved = self._resolve_reference_path(
                preparation.step_context,
                step_outcome.return_reference_path,
            )
            if inspect.isawaitable(resolved):
                raise ExecutionError("Sync Natural function cannot return an awaitable value. Use async def and await the function call.")
            return_value = self._parse_and_coerce_return_value(resolved, return_annotation)

        if step_outcome_kind == "raise":
            assert isinstance(step_outcome, RaiseStepOutcome)
            self._validate_raise_outcome(preparation.step_context, step_outcome)

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
        ):
            step_executor = self.step_executor

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

            step_outcome_kind = self._apply_bindings_and_validate_kind(
                step_context=preparation.step_context,
                step_outcome=step_outcome,
                bindings=bindings,
                allowed_step_kinds=preparation.allowed_step_kinds,
            )

            return_value: object | None = None
            if step_outcome_kind == "return":
                assert isinstance(step_outcome, ReturnStepOutcome)
                resolved = self._resolve_reference_path(
                    preparation.step_context,
                    step_outcome.return_reference_path,
                )
                if inspect.isawaitable(resolved):
                    resolved = await resolved
                return_value = self._parse_and_coerce_return_value(resolved, return_annotation)

            if step_outcome_kind == "raise":
                assert isinstance(step_outcome, RaiseStepOutcome)
                self._validate_raise_outcome(preparation.step_context, step_outcome)

            return StepEnvelope(
                step_outcome=step_outcome,
                input_bindings=dict(preparation.input_binding_name_to_value),
                bindings=bindings,
                return_value=return_value,
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
            ):
                step_outcome, bindings = self.step_executor.run_step(
                    processed_natural_program=preparation.processed_program,
                    step_context=preparation.step_context,
                    binding_names=output_binding_names,
                    allowed_step_kinds=preparation.allowed_step_kinds,
                )
                return self._finalize_step(
                    preparation=preparation,
                    step_outcome=step_outcome,
                    bindings=bindings,
                    return_annotation=return_annotation,
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
