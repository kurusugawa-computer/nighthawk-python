from __future__ import annotations

import ast
import inspect
import textwrap
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from .configuration import Configuration, ExecutionConfiguration
from .execution.context import (
    get_current_execution_context,
    get_execution_context_stack,
    python_name_scope,
)
from .execution.environment import (
    ExecutionEnvironment,
    environment,
    environment_override,
    get_environment,
)
from .execution.executors import AgentExecutor
from .execution.orchestrator import Orchestrator
from .natural.blocks import find_natural_blocks
from .natural.transform import transform_function_source
from .tools import call_scope, tool

F = TypeVar("F", bound=Callable[..., Any])


class _OrchestratorProxy:
    def run_natural_block(
        self,
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        return_annotation: object,
        is_in_loop: bool,
    ) -> dict[str, object]:
        frame = inspect.currentframe()
        if frame is None or frame.f_back is None:
            raise RuntimeError("No caller frame")
        caller_frame = frame.f_back

        current_environment = get_environment()
        orchestrator = Orchestrator.from_environment(current_environment)
        return orchestrator.run_natural_block(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            return_annotation,
            is_in_loop,
            caller_frame=caller_frame,
        )


def fn(func: F | None = None) -> F:
    if func is None:
        return lambda f: fn(f)  # type: ignore[return-value]

    lines, _ = inspect.getsourcelines(func)
    source = textwrap.dedent("".join(lines))

    try:
        module = ast.parse(source)
        for node in module.body:
            if isinstance(node, ast.FunctionDef) and node.name == func.__name__:
                node.decorator_list = []
                source = ast.unparse(module)
                break
    except Exception:
        pass

    def extract_template_interpolation_name_set(program_text: str) -> set[str]:
        try:
            parsed = ast.parse("t" + repr(program_text), mode="eval")
        except SyntaxError:
            return set()

        template_string = getattr(parsed, "body", None)
        values = getattr(template_string, "values", None)
        if not isinstance(values, list):
            return set()

        names: set[str] = set()

        class Visitor(ast.NodeVisitor):
            def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
                names.add(node.id)

        visitor = Visitor()
        for value in values:
            if isinstance(value, ast.Interpolation):
                visitor.visit(value.value)

        return names

    capture_name_set: set[str] = set()
    try:
        for block in find_natural_blocks(source):
            capture_name_set.update(block.input_bindings)
            capture_name_set.update(block.bindings)
            capture_name_set.update(extract_template_interpolation_name_set(block.text))
    except Exception:
        capture_name_set = set()

    definition_frame = inspect.currentframe()
    name_to_value: dict[str, object] = {}
    if definition_frame is not None and definition_frame.f_back is not None:
        caller_frame = definition_frame.f_back
        if caller_frame.f_code.co_name != "<module>":
            for name in capture_name_set:
                if name in caller_frame.f_locals:
                    name_to_value[name] = caller_frame.f_locals[name]

    transformed_source = transform_function_source(source)

    filename = inspect.getsourcefile(func) or "<nighthawk>"
    code = compile(transformed_source, filename, "exec")

    globals_namespace: dict[str, object] = dict(func.__globals__)
    globals_namespace["__nighthawk_orchestrator__"] = _OrchestratorProxy()

    module_namespace: dict[str, object] = {}
    exec(code, globals_namespace, module_namespace)

    transformed = module_namespace.get(func.__name__)
    if not callable(transformed):
        raise RuntimeError("Transformed function not found after compilation")

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with call_scope():
            if name_to_value:
                with python_name_scope(name_to_value):
                    return transformed(*args, **kwargs)
            return transformed(*args, **kwargs)

    return cast(F, wrapper)  # type: ignore[return-value]


__all__ = [
    "AgentExecutor",
    "Configuration",
    "ExecutionConfiguration",
    "ExecutionEnvironment",
    "environment",
    "environment_override",
    "fn",
    "get_current_execution_context",
    "get_environment",
    "get_execution_context_stack",
    "tool",
]
