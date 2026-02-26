from __future__ import annotations

import inspect
import uuid
from types import FrameType
from typing import Any, Awaitable, Callable, Coroutine, cast

import yaml
from pydantic import TypeAdapter

from ..errors import ExecutionError
from .async_bridge import run_coroutine_synchronously
from .environment import Environment
from .scoping import RUN_ID, SCOPE_ID, STEP_ID, span
from .step_context import (
    StepContext,
    get_python_cell_scope_stack,
    get_python_name_scope_stack,
    get_step_context_stack,
    resolve_name_in_step_context,
)
from .step_contract import STEP_KINDS, StepOutcome


def _split_frontmatter_or_none(processed_natural_program: str) -> tuple[str, tuple[str, ...]]:
    lines = processed_natural_program.splitlines(keepends=True)
    if not lines:
        return processed_natural_program, ()

    start_index: int | None = None
    for i, line in enumerate(lines):
        if line.strip(" \t\r\n") == "":
            continue
        start_index = i
        break

    if start_index is None:
        return processed_natural_program, ()

    first_line = lines[start_index]
    if first_line not in ("---\n", "---"):
        return processed_natural_program, ()

    closing_index: int | None = None
    for i, line in enumerate(lines[start_index + 1 :], start=start_index + 1):
        if line in ("---\n", "---"):
            closing_index = i
            break

    if closing_index is None:
        return processed_natural_program, ()

    yaml_text = "".join(lines[start_index + 1 : closing_index])
    if yaml_text.strip() == "":
        return processed_natural_program, ()

    try:
        loaded = yaml.safe_load(yaml_text)
    except Exception as e:
        raise ExecutionError(f"Frontmatter YAML parsing failed: {e}") from e

    if not isinstance(loaded, dict):
        raise ExecutionError("Frontmatter YAML must be a mapping")

    allowed_keys = {"deny"}
    unknown_keys = set(loaded.keys()) - allowed_keys
    if unknown_keys:
        unknown_key_list = ", ".join(sorted(str(k) for k in unknown_keys))
        raise ExecutionError(f"Unknown frontmatter keys: {unknown_key_list}")

    if "deny" not in loaded:
        raise ExecutionError("Frontmatter must include 'deny'")

    deny_value = loaded["deny"]
    if not isinstance(deny_value, list) or not all(isinstance(item, str) for item in deny_value):
        raise ExecutionError("Frontmatter 'deny' must be a YAML sequence of strings")

    if len(deny_value) == 0:
        raise ExecutionError("Frontmatter 'deny' must not be empty")

    denied: list[str] = []
    for item in deny_value:
        if item not in STEP_KINDS:
            raise ExecutionError(f"Unknown denied step kind: {item}")
        if item not in denied:
            denied.append(item)

    instructions_without_frontmatter = "".join(lines[closing_index + 1 :])
    return instructions_without_frontmatter, tuple(denied)


class Runner:
    def __init__(self, environment: Environment) -> None:
        self.environment = environment

    @classmethod
    def from_environment(cls, environment: Environment) -> "Runner":
        return cls(environment)

    def _parse_and_coerce_return_value(self, value: object, return_annotation: object) -> object:
        try:
            adapted = TypeAdapter(return_annotation)
            return adapted.validate_python(value)
        except Exception as e:
            raise ExecutionError(f"Return value validation failed: {e}") from e

    def _resolve_reference_path(self, step_context: StepContext, return_reference_path: str) -> object:
        parts = return_reference_path.split(".")
        if any(part == "" for part in parts):
            raise ExecutionError(f"Invalid return_reference_path: {return_reference_path!r}")

        for part in parts:
            try:
                part.encode("ascii")
            except UnicodeEncodeError:
                raise ExecutionError(f"Invalid return_reference_path segment (non-ASCII): {part!r}")
            if not part.isidentifier():
                raise ExecutionError(f"Invalid return_reference_path segment (not identifier): {part!r}")
            if part.startswith("__"):
                raise ExecutionError(f"Invalid return_reference_path segment (dunder): {part!r}")

        root_name = parts[0]
        if root_name not in step_context.step_locals:
            raise ExecutionError(f"Unknown root name in return_reference_path: {root_name}")
        current = step_context.step_locals[root_name]
        remaining = parts[1:]

        for part in remaining:
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
    ) -> tuple[str, tuple[str, ...], StepContext, dict[str, object]]:
        python_locals = caller_frame.f_locals
        python_globals = caller_frame.f_globals

        processed_without_frontmatter, denied_step_kinds = _split_frontmatter_or_none(natural_program)
        processed_without_frontmatter = processed_without_frontmatter.lstrip("\n")

        base_allowed_kinds: list[str] = ["pass", "return", "raise"]
        if is_in_loop:
            base_allowed_kinds.extend(["break", "continue"])

        allowed_step_kinds = tuple(kind for kind in base_allowed_kinds if kind not in denied_step_kinds)

        step_globals: dict[str, object] = dict(python_globals)
        if "__builtins__" not in step_globals:
            step_globals["__builtins__"] = __builtins__

        step_locals: dict[str, object] = {}

        step_context_stack = get_step_context_stack()
        if step_context_stack:
            step_locals.update(step_context_stack[-1].step_locals)

        step_locals.update(python_locals)

        binding_commit_targets = set(output_binding_names)

        local_variable_name_set = set(caller_frame.f_code.co_varnames)
        local_variable_name_set.update(caller_frame.f_code.co_cellvars)
        free_variable_name_set = set(caller_frame.f_code.co_freevars)

        python_cell_scope_stack = get_python_cell_scope_stack()
        python_name_scope_stack = get_python_name_scope_stack()

        python_builtins = python_globals.get("__builtins__", __builtins__)

        def resolve_input_binding_value(binding_name: str) -> tuple[object, str]:
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

        resolved_input_binding_name_to_value: dict[str, object] = {}
        resolved_input_binding_name_to_resolution_kind: dict[str, str] = {}
        for binding_name in input_binding_names:
            value, resolution_kind = resolve_input_binding_value(binding_name)
            resolved_input_binding_name_to_value[binding_name] = value
            resolved_input_binding_name_to_resolution_kind[binding_name] = resolution_kind

        for binding_name, resolution_kind in resolved_input_binding_name_to_resolution_kind.items():
            if resolution_kind in ("locals", "cell_scope", "name_scope"):
                step_locals[binding_name] = resolved_input_binding_name_to_value[binding_name]

        step_context = StepContext(
            step_id=str(uuid.uuid4()),
            run_configuration=self.environment.run_configuration,
            step_globals=step_globals,
            step_locals=step_locals,
            binding_commit_targets=binding_commit_targets,
            binding_name_to_type=binding_name_to_type,
        )

        return processed_without_frontmatter, allowed_step_kinds, step_context, dict(resolved_input_binding_name_to_value)

    def _validate_raise_outcome(self, step_context: StepContext, *, raise_error_type_name: str | None, raise_message: str) -> None:
        if raise_error_type_name is not None:
            resolved_raise_error_type = resolve_name_in_step_context(step_context, raise_error_type_name)
            if resolved_raise_error_type is None:
                raise ExecutionError(f"Invalid raise_error_type: {raise_error_type_name!r}")
            if not isinstance(resolved_raise_error_type, type) or not issubclass(resolved_raise_error_type, BaseException):
                raise ExecutionError(f"Invalid raise_error_type: {raise_error_type_name!r}")
            raise resolved_raise_error_type(raise_message)

        raise ExecutionError(f"Execution failed: {raise_message}")

    def _build_envelope_sync(
        self,
        *,
        step_context: StepContext,
        step_outcome: object,
        allowed_step_kinds: tuple[str, ...],
        bindings: dict[str, object],
        input_bindings: dict[str, object],
        return_annotation: object,
    ) -> dict[str, object]:
        if not hasattr(step_outcome, "kind"):
            raise ExecutionError("Step produced invalid step outcome object")

        step_outcome_kind = getattr(step_outcome, "kind")
        if step_outcome_kind not in allowed_step_kinds:
            raise ExecutionError(f"Step '{step_outcome_kind}' is not allowed for this step. Allowed kinds: {allowed_step_kinds}")

        step_context.step_locals.update(bindings)

        return_value: object | None = None

        if step_outcome_kind == "return":
            return_reference_path = getattr(step_outcome, "return_reference_path")
            resolved = self._resolve_reference_path(step_context, return_reference_path)
            if inspect.isawaitable(resolved):
                raise ExecutionError("Sync Natural function cannot return an awaitable value. Use async def and await the function call.")
            return_value = self._parse_and_coerce_return_value(resolved, return_annotation)

        if step_outcome_kind == "raise":
            self._validate_raise_outcome(
                step_context,
                raise_error_type_name=getattr(step_outcome, "raise_error_type"),
                raise_message=getattr(step_outcome, "raise_message"),
            )

        return {
            "step_outcome": step_outcome,
            "input_bindings": dict(input_bindings),
            "bindings": bindings,
            "return_value": return_value,
        }

    async def _build_envelope_async(
        self,
        *,
        step_context: StepContext,
        step_outcome: object,
        allowed_step_kinds: tuple[str, ...],
        bindings: dict[str, object],
        input_bindings: dict[str, object],
        return_annotation: object,
    ) -> dict[str, object]:
        if not hasattr(step_outcome, "kind"):
            raise ExecutionError("Step produced invalid step outcome object")

        step_outcome_kind = getattr(step_outcome, "kind")
        if step_outcome_kind not in allowed_step_kinds:
            raise ExecutionError(f"Step '{step_outcome_kind}' is not allowed for this step. Allowed kinds: {allowed_step_kinds}")

        step_context.step_locals.update(bindings)

        return_value: object | None = None

        if step_outcome_kind == "return":
            return_reference_path = getattr(step_outcome, "return_reference_path")
            resolved = self._resolve_reference_path(step_context, return_reference_path)
            if inspect.isawaitable(resolved):
                resolved = await resolved
            return_value = self._parse_and_coerce_return_value(resolved, return_annotation)

        if step_outcome_kind == "raise":
            self._validate_raise_outcome(
                step_context,
                raise_error_type_name=getattr(step_outcome, "raise_error_type"),
                raise_message=getattr(step_outcome, "raise_message"),
            )

        return {
            "step_outcome": step_outcome,
            "input_bindings": dict(input_bindings),
            "bindings": bindings,
            "return_value": return_value,
        }

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
    ) -> dict[str, object]:
        processed_without_frontmatter, allowed_step_kinds, step_context, input_bindings = self._prepare_step_execution(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            is_in_loop,
            caller_frame=caller_frame,
        )

        with span(
            "nighthawk.step",
            **{
                RUN_ID: self.environment.run_id,
                SCOPE_ID: self.environment.scope_id,
                STEP_ID: step_context.step_id,
            },
        ):
            step_executor = self.environment.step_executor

            run_step_method = getattr(step_executor, "run_step", None)
            if callable(run_step_method):
                run_step_method_typed = cast(
                    Callable[..., tuple[StepOutcome, dict[str, object]]],
                    run_step_method,
                )
                step_outcome, bindings = run_step_method_typed(
                    processed_natural_program=processed_without_frontmatter,
                    step_context=step_context,
                    binding_names=output_binding_names,
                    allowed_step_kinds=allowed_step_kinds,
                )
            else:
                run_step_async_method = getattr(step_executor, "run_step_async", None)
                if not callable(run_step_async_method):
                    raise ExecutionError("Step executor must define run_step(...) or run_step_async(...)")
                run_step_async_method_typed = cast(
                    Callable[..., Awaitable[tuple[StepOutcome, dict[str, object]]]],
                    run_step_async_method,
                )
                step_outcome, bindings = run_coroutine_synchronously(
                    lambda: cast(
                        Coroutine[Any, Any, tuple[StepOutcome, dict[str, object]]],
                        run_step_async_method_typed(
                            processed_natural_program=processed_without_frontmatter,
                            step_context=step_context,
                            binding_names=output_binding_names,
                            allowed_step_kinds=allowed_step_kinds,
                        ),
                    )
                )

            return self._build_envelope_sync(
                step_context=step_context,
                step_outcome=step_outcome,
                allowed_step_kinds=allowed_step_kinds,
                bindings=bindings,
                input_bindings=input_bindings,
                return_annotation=return_annotation,
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
    ) -> dict[str, object]:
        processed_without_frontmatter, allowed_step_kinds, step_context, input_bindings = self._prepare_step_execution(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            is_in_loop,
            caller_frame=caller_frame,
        )

        with span(
            "nighthawk.step",
            **{
                RUN_ID: self.environment.run_id,
                SCOPE_ID: self.environment.scope_id,
                STEP_ID: step_context.step_id,
            },
        ):
            step_executor = self.environment.step_executor

            run_step_async_method = getattr(step_executor, "run_step_async", None)
            if callable(run_step_async_method):
                run_step_async_method_typed = cast(
                    Callable[..., Awaitable[tuple[StepOutcome, dict[str, object]]]],
                    run_step_async_method,
                )
                step_outcome, bindings = await run_step_async_method_typed(
                    processed_natural_program=processed_without_frontmatter,
                    step_context=step_context,
                    binding_names=output_binding_names,
                    allowed_step_kinds=allowed_step_kinds,
                )
            else:
                run_step_method = getattr(step_executor, "run_step", None)
                if not callable(run_step_method):
                    raise ExecutionError("Step executor must define run_step_async(...) or run_step(...)")

                run_step_method_typed = cast(
                    Callable[..., tuple[StepOutcome, dict[str, object]]],
                    run_step_method,
                )
                step_outcome, bindings = run_step_method_typed(
                    processed_natural_program=processed_without_frontmatter,
                    step_context=step_context,
                    binding_names=output_binding_names,
                    allowed_step_kinds=allowed_step_kinds,
                )

            return await self._build_envelope_async(
                step_context=step_context,
                step_outcome=step_outcome,
                allowed_step_kinds=allowed_step_kinds,
                bindings=bindings,
                input_bindings=input_bindings,
                return_annotation=return_annotation,
            )


def get_caller_frame() -> FrameType:
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:
        raise ExecutionError("No caller frame")
    return frame.f_back
