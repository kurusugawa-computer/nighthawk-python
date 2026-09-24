"""Prepare namespaces and contracts before admitting a Natural step to an executor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import FrameType

from ..errors import ExecutionError, NaturalParseError
from ..natural.blocks import parse_frontmatter, validate_frontmatter_deny
from .annotations import SourceAnnotation, module_annotations, resolve_contract
from .bindings import MISSING_ANNOTATION, infer_binding_types, normalize_binding_names
from .description import discover_type_alias_reference_names
from .environment import StepEnvironment
from .scoping import _current_task_references, get_execution_reference
from .step_context import StepContext, ToolResultRenderingPolicy, get_python_cell_scope_stack, get_step_context_stack
from .step_contract import StepKind
from .step_executor import StepExecutor


def _split_frontmatter(
    processed_natural_program: str,
) -> tuple[str, tuple[str, ...]]:
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


def _build_step_globals(
    python_globals: dict[str, object],
) -> dict[str, object]:
    step_globals: dict[str, object] = dict(python_globals)
    if "__builtins__" not in step_globals:
        step_globals["__builtins__"] = __builtins__
    return step_globals


@dataclass(frozen=True)
class PreparedStep:
    processed_program: str
    allowed_step_kinds: tuple[StepKind, ...]
    step_context: StepContext
    input_binding_name_to_value: dict[str, object]
    output_binding_names: tuple[str, ...]
    binding_name_to_type: Mapping[str, object]


class StepPreparation:
    """Own preparation and the partial facts available for failure reporting."""

    def __init__(self, step_executor: StepExecutor, runtime_name_set: frozenset[str]) -> None:
        self.step_executor = step_executor
        self.runtime_name_set = runtime_name_set
        self.processed_natural_program: str | None = None
        self.allowed_step_kinds: tuple[StepKind, ...] | None = None
        self.input_binding_name_to_value: dict[str, object] | None = None

    @staticmethod
    def resolve_step_annotation(annotation: object, *, caller_frame: FrameType | None, environment: StepEnvironment) -> object:
        if caller_frame is not None:
            return resolve_contract(annotation, python_globals=caller_frame.f_globals, python_locals=dict(caller_frame.f_locals))
        namespace = environment.expression_namespace()
        return resolve_contract(annotation, python_globals=namespace, python_locals=namespace)

    def _prepare_python_environment(
        self,
        caller_frame: FrameType,
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        scoped_task_name_to_value: Mapping[str, object],
    ) -> tuple[StepEnvironment, dict[str, object]]:
        python_locals = caller_frame.f_locals
        python_globals = caller_frame.f_globals
        step_locals = {name: value for name, value in python_locals.items() if name not in self.runtime_name_set}
        parent_stack = get_step_context_stack()
        parent_name_to_value = parent_stack[-1].environment.snapshot_working_state() if parent_stack else {}
        cell_scope_stack = get_python_cell_scope_stack()
        python_name_to_cell = cell_scope_stack[-1] if cell_scope_stack else {}

        local_name_set = set(caller_frame.f_code.co_varnames) | set(caller_frame.f_code.co_cellvars)
        cell_name_set = set(caller_frame.f_code.co_freevars)
        module_name_to_annotation = module_annotations(
            python_globals, resolve_deferred=any(name not in local_name_set | cell_name_set for name in output_binding_names)
        )
        binding_name_to_annotation = dict(module_name_to_annotation)
        binding_name_to_annotation.update(
            {name: annotation for name, annotation in binding_name_to_type.items() if annotation is not MISSING_ANNOTATION}
        )
        binding_name_to_type = {name: binding_name_to_type.get(name, MISSING_ANNOTATION) for name in output_binding_names}
        for name in output_binding_names:
            annotation = binding_name_to_type.get(name, MISSING_ANNOTATION)
            if name not in local_name_set | cell_name_set:
                if name in module_name_to_annotation:
                    annotation = module_name_to_annotation[name]
                    if isinstance(annotation, str):
                        annotation = SourceAnnotation((annotation,))
                if name in python_globals:
                    step_locals[name] = python_globals[name]
            if annotation is not MISSING_ANNOTATION:
                annotation = resolve_contract(annotation, python_globals=caller_frame.f_globals, python_locals=dict(caller_frame.f_locals))
            binding_name_to_type[name] = annotation

        binding_name_to_type = infer_binding_types(binding_name_to_type, step_locals)
        environment = StepEnvironment(
            python_name_to_value=python_locals,
            python_globals=python_globals,
            local_name_set=frozenset(local_name_set),
            python_name_to_cell=python_name_to_cell,
            parent_name_to_value=parent_name_to_value,
            task_name_to_value=scoped_task_name_to_value,
            output_name_set=frozenset(output_binding_names),
            binding_name_to_annotation=binding_name_to_annotation,
            binding_name_to_type=binding_name_to_type,
            runtime_name_set=self.runtime_name_set,
        )
        return environment, step_locals

    def _prepare_explicit_environment(
        self,
        name_to_value: Mapping[str, object],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        scoped_task_name_to_value: Mapping[str, object],
    ) -> tuple[StepEnvironment, dict[str, object]]:
        step_locals = dict(name_to_value)
        binding_name_to_annotation = {name: annotation for name, annotation in binding_name_to_type.items() if annotation is not MISSING_ANNOTATION}
        environment = StepEnvironment(
            python_name_to_value=step_locals,
            python_globals={},
            task_name_to_value=scoped_task_name_to_value,
            output_name_set=frozenset(output_binding_names),
        )
        selected_name_to_type = {
            name: self.resolve_step_annotation(binding_name_to_type[name], caller_frame=None, environment=environment)
            if name in binding_name_to_type and binding_name_to_type[name] is not MISSING_ANNOTATION
            else MISSING_ANNOTATION
            for name in output_binding_names
        }
        selected_name_to_type = infer_binding_types(selected_name_to_type, step_locals)
        environment = StepEnvironment(
            python_name_to_value=step_locals,
            python_globals={},
            task_name_to_value=scoped_task_name_to_value,
            output_name_set=frozenset(output_binding_names),
            binding_name_to_annotation=binding_name_to_annotation,
            binding_name_to_type=selected_name_to_type,
        )
        return environment, step_locals

    def prepare(
        self,
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        is_in_loop: bool,
        *,
        caller_frame: FrameType | None,
        name_to_value: Mapping[str, object] | None,
    ) -> PreparedStep:
        normalize_binding_names(input_binding_names)
        normalize_binding_names(output_binding_names)
        processed_without_frontmatter, denied_step_kinds = _split_frontmatter(natural_program)
        processed_without_frontmatter = processed_without_frontmatter.lstrip("\n")
        self.processed_natural_program = processed_without_frontmatter
        allowed_step_kinds = _compute_allowed_step_kinds(is_in_loop, denied_step_kinds)
        self.allowed_step_kinds = allowed_step_kinds
        scoped_task_name_to_value = _current_task_references()
        if name_to_value is None:
            assert caller_frame is not None
            environment, step_locals = self._prepare_python_environment(
                caller_frame, output_binding_names, binding_name_to_type, scoped_task_name_to_value
            )
            step_globals = _build_step_globals(caller_frame.f_globals)
            self.input_binding_name_to_value = {name: environment.resolve(name) for name in input_binding_names}
        else:
            environment, step_locals = self._prepare_explicit_environment(
                name_to_value, output_binding_names, binding_name_to_type, scoped_task_name_to_value
            )
            step_globals = _build_step_globals({})
            for name in input_binding_names:
                if name not in step_locals:
                    raise NameError(f"Read binding {name!r} is absent from the explicit namespace", name=name)
            self.input_binding_name_to_value = {name: step_locals[name] for name in input_binding_names}
        binding_name_to_type = dict(environment.binding_name_to_type)
        tool_result_rendering_policy = getattr(self.step_executor, "tool_result_rendering_policy", None)
        if tool_result_rendering_policy is not None and not isinstance(tool_result_rendering_policy, ToolResultRenderingPolicy):
            raise ExecutionError("Step executor tool_result_rendering_policy must be ToolResultRenderingPolicy when provided")

        binding_commit_targets = set(output_binding_names)
        read_binding_names = frozenset(input_binding_names) - binding_commit_targets
        type_alias_reference_names = discover_type_alias_reference_names(
            step_locals=step_locals,
            step_globals=step_globals,
            input_binding_names=input_binding_names,
        )

        step_context = StepContext(
            execution_reference=get_execution_reference(),
            step_globals=step_globals,
            step_locals=step_locals,
            binding_commit_targets=binding_commit_targets,
            read_binding_names=read_binding_names,
            task_name_to_value=scoped_task_name_to_value,
            type_alias_reference_names=type_alias_reference_names,
            processed_natural_program=processed_without_frontmatter,
            binding_name_to_type=binding_name_to_type,
            tool_result_rendering_policy=tool_result_rendering_policy,
        )
        step_context.environment = environment

        input_binding_name_to_value = self.input_binding_name_to_value
        return PreparedStep(
            processed_program=processed_without_frontmatter,
            allowed_step_kinds=allowed_step_kinds,
            step_context=step_context,
            input_binding_name_to_value=input_binding_name_to_value,
            output_binding_names=tuple(output_binding_names),
            binding_name_to_type=step_context.binding_name_to_type,
        )
