from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from types import FrameType
from typing import cast

import yaml
from pydantic import BaseModel, TypeAdapter

from ..errors import ExecutionError
from .context import (
    ExecutionContext,
    get_execution_context_stack,
    get_python_cell_scope_stack,
    get_python_name_scope_stack,
)
from .contracts import EXECUTION_EFFECT_TYPES, ExecutionFinal
from .environment import ExecutionEnvironment


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
        if item not in EXECUTION_EFFECT_TYPES:
            raise ExecutionError(f"Unknown denied effect: {item}")
        if item not in denied:
            denied.append(item)

    instructions_without_frontmatter = "".join(lines[closing_index + 1 :])
    return instructions_without_frontmatter, tuple(denied)


@dataclass
class Orchestrator:
    environment: ExecutionEnvironment

    @classmethod
    def from_environment(cls, environment: ExecutionEnvironment) -> "Orchestrator":
        return cls(environment=environment)

    def _parse_and_coerce_return_value(self, value: object, return_annotation: object) -> object:
        try:
            adapted = TypeAdapter(return_annotation)
            return adapted.validate_python(value)
        except Exception as e:
            raise ExecutionError(f"Return value validation failed: {e}") from e

    def _resolve_reference_path(self, execution_context: ExecutionContext, source_path: str) -> object:
        parts = source_path.split(".")
        if any(part == "" for part in parts):
            raise ExecutionError(f"Invalid source_path: {source_path!r}")

        for part in parts:
            try:
                part.encode("ascii")
            except UnicodeEncodeError:
                raise ExecutionError(f"Invalid source_path segment (non-ASCII): {part!r}")
            if not part.isidentifier():
                raise ExecutionError(f"Invalid source_path segment (not identifier): {part!r}")
            if part.startswith("__"):
                raise ExecutionError(f"Invalid source_path segment (dunder): {part!r}")

        root_name = parts[0]
        if root_name not in execution_context.execution_locals:
            raise ExecutionError(f"Unknown root name in source_path: {root_name}")
        current = execution_context.execution_locals[root_name]
        remaining = parts[1:]

        for part in remaining:
            try:
                current = getattr(current, part)
                continue
            except Exception:
                pass

            if isinstance(current, dict):
                try:
                    current = current[part]
                    continue
                except Exception:
                    pass

            raise ExecutionError(f"Failed to resolve source_path segment {part!r} in {source_path!r}")

        return current

    def run_natural_block(
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
        python_locals = caller_frame.f_locals
        python_globals = caller_frame.f_globals

        processed_without_frontmatter, denied_effect_types = _split_frontmatter_or_none(natural_program)
        processed_without_frontmatter = processed_without_frontmatter.lstrip("\n")

        base_allowed_effect_types = ["return"]
        if is_in_loop:
            base_allowed_effect_types.extend(["break", "continue"])

        allowed_effect_types = tuple(effect_type for effect_type in base_allowed_effect_types if effect_type not in denied_effect_types)

        execution_globals: dict[str, object] = {"__builtins__": __builtins__}

        execution_locals: dict[str, object] = {}

        execution_context_stack = get_execution_context_stack()
        if execution_context_stack:
            execution_locals.update(execution_context_stack[-1].execution_locals)

        execution_locals.update(python_locals)

        binding_commit_targets = set(output_binding_names)

        local_variable_name_set = set(caller_frame.f_code.co_varnames)
        local_variable_name_set.update(caller_frame.f_code.co_cellvars)
        free_variable_name_set = set(caller_frame.f_code.co_freevars)

        python_cell_scope_stack = get_python_cell_scope_stack()
        python_name_scope_stack = get_python_name_scope_stack()

        python_builtins = python_globals.get("__builtins__", __builtins__)

        def resolve_input_binding_value(binding_name: str) -> object:
            if binding_name in python_locals:
                return python_locals[binding_name]

            for scope in reversed(python_cell_scope_stack):
                if binding_name not in scope:
                    continue
                cell = scope[binding_name]
                try:
                    return cell.cell_contents
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
                    return scope[binding_name]

            if binding_name in python_globals:
                return python_globals[binding_name]

            if isinstance(python_builtins, dict) and binding_name in python_builtins:
                return python_builtins[binding_name]

            if hasattr(python_builtins, binding_name):
                return getattr(python_builtins, binding_name)

            error = NameError(f"name {binding_name!r} is not defined")
            error.name = binding_name
            raise error

        resolved_input_binding_name_to_value: dict[str, object] = {}
        for binding_name in input_binding_names:
            resolved_input_binding_name_to_value[binding_name] = resolve_input_binding_value(binding_name)

        execution_locals.update(resolved_input_binding_name_to_value)

        execution_context = ExecutionContext(
            execution_id=str(uuid.uuid4()),
            execution_configuration=self.environment.execution_configuration,
            execution_globals=execution_globals,
            execution_locals=execution_locals,
            binding_commit_targets=binding_commit_targets,
            binding_name_to_type=binding_name_to_type,
        )

        final: object
        effect_value: object | None = None

        final, bindings = self.environment.execution_executor.run_natural_block(
            processed_natural_program=processed_without_frontmatter,
            execution_context=execution_context,
            binding_names=output_binding_names,
            is_in_loop=is_in_loop,
            allowed_effect_types=allowed_effect_types,
        )

        input_bindings = dict(resolved_input_binding_name_to_value)

        execution_context.execution_locals.update(bindings)

        final_typed = final
        if not isinstance(final_typed, ExecutionFinal):
            if isinstance(final_typed, BaseModel):
                final_typed = ExecutionFinal.model_validate(final_typed.model_dump())
            else:
                final_typed = ExecutionFinal.model_validate(final_typed)

        if not isinstance(final_typed, ExecutionFinal):
            raise ExecutionError("Execution produced unexpected final type")

        final_typed_execution = cast(ExecutionFinal, final_typed)

        effect = final_typed_execution.effect
        if effect is not None:
            if effect.type not in allowed_effect_types:
                deny_message = f"Effect '{effect.type}' is not allowed for this Natural block. Allowed effects: {allowed_effect_types}"
                raise ExecutionError(deny_message)

            if effect.type == "return":
                if effect.source_path is None:
                    effect_value = self._parse_and_coerce_return_value(None, return_annotation)
                else:
                    resolved = self._resolve_reference_path(execution_context, effect.source_path)
                    effect_value = self._parse_and_coerce_return_value(resolved, return_annotation)

        return {
            "execution_final": final_typed,
            "input_bindings": dict(input_bindings),
            "bindings": bindings,
            "effect_value": effect_value,
        }


def get_caller_frame() -> FrameType:
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:
        raise ExecutionError("No caller frame")
    return frame.f_back
