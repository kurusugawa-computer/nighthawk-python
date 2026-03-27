from __future__ import annotations

import ast
import inspect
import logging
import sys
import textwrap
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, cast

from ..runtime.runner import Runner, StepEnvelope
from ..runtime.scoping import get_step_executor
from ..runtime.step_context import python_cell_scope, python_name_scope
from ..tools.registry import call_scope
from .blocks import find_natural_blocks
from .transform import transform_module_ast

type NaturalFunctionCallable = Callable[..., Any]


class _RunnerProxy:
    @staticmethod
    def run_step(
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        return_annotation: object,
        is_in_loop: bool,
    ) -> StepEnvelope:
        caller_frame = sys._getframe(1)
        current_step_executor = get_step_executor()
        runner = Runner(current_step_executor)
        return runner.run_step(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            return_annotation,
            is_in_loop,
            caller_frame=caller_frame,
        )

    @staticmethod
    async def run_step_async(
        natural_program: str,
        input_binding_names: list[str],
        output_binding_names: list[str],
        binding_name_to_type: dict[str, object],
        return_annotation: object,
        is_in_loop: bool,
    ) -> StepEnvelope:
        caller_frame = sys._getframe(1)
        current_step_executor = get_step_executor()
        runner = Runner(current_step_executor)
        return await runner.run_step_async(
            natural_program,
            input_binding_names,
            output_binding_names,
            binding_name_to_type,
            return_annotation,
            is_in_loop,
            caller_frame=caller_frame,
        )


def _extract_inline_fstring_name_set(function_source: str, *, function_name: str) -> set[str]:
    """Extract names referenced in f-string expressions of inline Natural blocks."""
    try:
        module = ast.parse(function_source)
    except SyntaxError:
        return set()

    function_def: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            function_def = node
            break
    if function_def is None:
        return set()

    names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
            names.add(node.id)

    visitor = Visitor()

    for statement in function_def.body:
        if not isinstance(statement, ast.Expr):
            continue
        value = statement.value
        if not isinstance(value, ast.JoinedStr):
            continue

        first_part: ast.expr | None = value.values[0] if value.values else None
        if not isinstance(first_part, ast.Constant) or not isinstance(first_part.value, str):
            continue
        if not first_part.value.startswith("natural\n"):
            continue

        for part in value.values:
            if isinstance(part, ast.FormattedValue):
                visitor.visit(part.value)

    return names


def _build_capture_name_set(source: str, function_name: str) -> set[str]:
    """Build the set of names that need to be captured from the enclosing scope."""
    capture_name_set: set[str] = set()
    try:
        for block in find_natural_blocks(source):
            capture_name_set.update(block.input_bindings)
            capture_name_set.update(block.output_bindings)
        capture_name_set.update(_extract_inline_fstring_name_set(source, function_name=function_name))
    except Exception as exception:
        logging.getLogger("nighthawk").warning("Failed to extract capture names for %s: %s", function_name, exception)
        capture_name_set = set()
    return capture_name_set


def _build_transformed_factory_module(
    *,
    transformed_module: ast.Module,
    function_name: str,
    name_to_value: dict[str, object],
) -> ast.Module:
    """Build a factory-function module that captures enclosing-scope values via closure."""
    transformed_function_def: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in transformed_module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            transformed_function_def = node
            break

    if transformed_function_def is None:
        raise RuntimeError("Transformed function not found in transformed module")

    captured_value_name = "__nh_captured_values__"
    factory_name = "__nh_factory__"

    factory_body: list[ast.stmt] = []
    for name in sorted(name_to_value.keys()):
        factory_body.append(
            ast.Assign(
                targets=[ast.Name(id=name, ctx=ast.Store())],
                value=ast.Subscript(
                    value=ast.Name(id=captured_value_name, ctx=ast.Load()),
                    slice=ast.Constant(name),
                    ctx=ast.Load(),
                ),
            )
        )

    factory_body.append(transformed_function_def)
    factory_body.append(ast.Return(value=ast.Name(id=function_name, ctx=ast.Load())))

    factory_function_def = ast.FunctionDef(
        name=factory_name,
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg=captured_value_name)],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=factory_body,
        decorator_list=[],
        returns=None,
        type_comment=None,
    )

    factory_module = ast.Module(body=[factory_function_def], type_ignores=[])
    ast.fix_missing_locations(factory_module)
    return factory_module


def natural_function(func: NaturalFunctionCallable | None = None) -> NaturalFunctionCallable:
    """Transform a function containing Natural blocks into an executable Natural function.

    Parses the function source to find Natural blocks, rewrites the AST to
    delegate block execution to the active step executor at runtime.

    Args:
        func: The function to transform. Can be omitted for use as a bare
            decorator.

    Example:
        ```python
        @nighthawk.natural_function
        def summarize(text: str) -> str:
            '''natural
            Summarize <text> in one sentence and assign it to <:result>.
            '''
            return result
        ```
    """
    if func is None:
        return lambda f: natural_function(f)  # type: ignore[return-value]

    if isinstance(func, staticmethod):
        decorated_static_function = natural_function(func.__func__)
        return cast(NaturalFunctionCallable, staticmethod(decorated_static_function))

    if isinstance(func, classmethod):
        decorated_class_function = natural_function(func.__func__)
        return cast(NaturalFunctionCallable, classmethod(decorated_class_function))

    lines, starting_line_number = inspect.getsourcelines(func)
    source = textwrap.dedent("".join(lines))

    try:
        original_module = ast.parse(source)
        for node in original_module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func.__name__:
                node.decorator_list = []
                break
        ast.increment_lineno(original_module, starting_line_number - 1)
    except Exception as exception:
        logging.getLogger("nighthawk").warning("Failed to parse original module AST for %s: %s", func.__name__, exception)
        original_module = ast.Module(body=[], type_ignores=[])

    capture_name_set = _build_capture_name_set(source, func.__name__)

    definition_frame = inspect.currentframe()
    name_to_value: dict[str, object] = {}
    if definition_frame is not None and definition_frame.f_back is not None:
        caller_frame = definition_frame.f_back
        if caller_frame.f_code.co_name != "<module>":
            for name in capture_name_set:
                if name in caller_frame.f_locals:
                    name_to_value[name] = caller_frame.f_locals[name]

    captured_name_tuple = tuple(sorted(capture_name_set))

    transformed_module = transform_module_ast(original_module, captured_name_tuple=captured_name_tuple)

    filename = inspect.getsourcefile(func) or "<nighthawk>"

    factory_module = _build_transformed_factory_module(
        transformed_module=transformed_module,
        function_name=func.__name__,
        name_to_value=name_to_value,
    )
    code = compile(factory_module, filename, "exec")

    globals_namespace: dict[str, object] = dict(func.__globals__)
    globals_namespace["__nighthawk_runner__"] = _RunnerProxy()
    from .blocks import extract_program as _nh_extract_program

    globals_namespace["__nh_extract_program__"] = _nh_extract_program
    globals_namespace["__nh_python_cell_scope__"] = python_cell_scope

    module_namespace: dict[str, object] = {}
    exec(code, globals_namespace, module_namespace)

    factory = module_namespace.get("__nh_factory__")
    if not callable(factory):
        raise RuntimeError("Transformed factory not found after compilation")

    transformed = factory(name_to_value)
    if not callable(transformed):
        raise RuntimeError("Transformed function not found after factory execution")

    transformed_freevar_name_set = set(transformed.__code__.co_freevars)
    captured_name_set = set(name_to_value.keys())

    unexpected_freevar_name_set = transformed_freevar_name_set - captured_name_set
    allowed_unexpected_freevar_name_set = {func.__name__}
    if not unexpected_freevar_name_set.issubset(allowed_unexpected_freevar_name_set):
        raise RuntimeError(
            f"Transformed function freevars do not match captured names. freevars={transformed.__code__.co_freevars!r} captured={tuple(sorted(name_to_value.keys()))!r}"
        )

    if transformed.__closure__ is None and name_to_value:
        raise RuntimeError("Transformed function closure is missing for captured names")

    if inspect.iscoroutinefunction(func):
        transformed_async = cast(Callable[..., Awaitable[Any]], transformed)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with call_scope():
                if name_to_value:
                    with python_name_scope(name_to_value):
                        return await transformed_async(*args, **kwargs)
                return await transformed_async(*args, **kwargs)

        return cast(NaturalFunctionCallable, async_wrapper)  # type: ignore[return-value]

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with call_scope():
            if name_to_value:
                with python_name_scope(name_to_value):
                    return transformed(*args, **kwargs)
            return transformed(*args, **kwargs)

    return cast(NaturalFunctionCallable, wrapper)  # type: ignore[return-value]
