from __future__ import annotations

import ast
import inspect
import logging
import sys
import textwrap
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from functools import wraps
from types import CellType, FunctionType
from typing import Any, cast

from ..errors import NighthawkError
from ..runtime.annotations import MISSING_ANNOTATION, SourceAnnotation
from ..runtime.runner import Runner
from ..runtime.scoping import get_step_executor
from ..runtime.step_context import python_cell_scope
from .blocks import extract_program
from .transform import transform_module_ast

type NaturalFunctionCallable = Callable[..., Any]


class _RunnerProxy:
    @staticmethod
    def execution() -> AbstractContextManager[Runner]:
        caller_frame = sys._getframe(1)
        runner = Runner(get_step_executor())
        return runner.execution(caller_frame=caller_frame)


def _function_name_to_cell(function: FunctionType) -> dict[str, CellType]:
    return dict(zip(function.__code__.co_freevars, function.__closure__ or (), strict=True))


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

    # Defaults already ran when Python created the original function.
    # Compile placeholders and restore their original live values on the new body.
    transformed_function_def.args.defaults = [ast.Constant(None) for _ in transformed_function_def.args.defaults]
    transformed_function_def.args.kw_defaults = [None if default is None else ast.Constant(None) for default in transformed_function_def.args.kw_defaults]

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

    if sys.implementation.name != "cpython" or sys.version_info < (3, 13):
        raise NighthawkError("Natural source instrumentation requires CPython 3.13 or later")
    try:
        lines, starting_line_number = inspect.getsourcelines(func)
    except (OSError, TypeError) as exception:
        raise NighthawkError("Natural functions require available Python def or async def source") from exception
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

    capture_name_set = set(func.__code__.co_freevars) | set(func.__code__.co_cellvars)

    name_to_value: dict[str, object] = {}
    original_name_to_cell = dict(zip(func.__code__.co_freevars, func.__closure__ or (), strict=True))
    for name, cell in original_name_to_cell.items():
        try:
            name_to_value[name] = cell.cell_contents
        except ValueError:
            name_to_value[name] = None

    captured_name_tuple = tuple(sorted(capture_name_set))

    transformed_module = transform_module_ast(original_module, captured_name_tuple=captured_name_tuple)

    filename = inspect.getsourcefile(func) or "<nighthawk>"

    # Runtime helpers referenced by the transformed body travel through the factory closure, not through
    # module globals. The transformed function therefore shares ``func.__globals__`` with the original
    # function: names defined in the module after decoration stay visible, and the module namespace is
    # never polluted with helper names.
    helper_name_to_value: dict[str, object] = {
        "__nighthawk_runner__": _RunnerProxy(),
        "__nh_extract_program__": extract_program,
        "__nh_python_cell_scope__": python_cell_scope,
        "__nh_source_annotation__": SourceAnnotation,
        "__nh_missing_annotation__": MISSING_ANNOTATION,
        "__nh_function_name_to_cell__": _function_name_to_cell,
    }
    factory_name_to_value: dict[str, object] = {**name_to_value, **helper_name_to_value}

    factory_module = _build_transformed_factory_module(
        transformed_module=transformed_module,
        function_name=func.__name__,
        name_to_value=factory_name_to_value,
    )
    code = compile(factory_module, filename, "exec")

    module_namespace: dict[str, object] = {}
    exec(code, func.__globals__, module_namespace)

    factory = module_namespace.get("__nh_factory__")
    if not callable(factory):
        raise RuntimeError("Transformed factory not found after compilation")

    transformed = factory(factory_name_to_value)
    if not callable(transformed):
        raise RuntimeError("Transformed function not found after factory execution")

    if transformed.__closure__ is not None:
        # Preserve original Python cells, including nonlocal writes and late rebinding.
        transformed = FunctionType(
            transformed.__code__,
            transformed.__globals__,
            transformed.__name__,
            func.__defaults__,
            tuple(
                original_name_to_cell.get(name, cell)
                for name, cell in zip(
                    transformed.__code__.co_freevars,
                    transformed.__closure__,
                    strict=True,
                )
            ),
        )
        transformed.__kwdefaults__ = func.__kwdefaults__
        transformed.__annotations__ = func.__annotations__

    transformed_freevar_name_set = set(transformed.__code__.co_freevars)
    captured_name_set = set(factory_name_to_value.keys())

    unexpected_freevar_name_set = transformed_freevar_name_set - captured_name_set
    allowed_unexpected_freevar_name_set = {func.__name__}
    if not unexpected_freevar_name_set.issubset(allowed_unexpected_freevar_name_set):
        raise RuntimeError(
            f"Transformed function freevars do not match captured names. freevars={transformed.__code__.co_freevars!r} captured={tuple(sorted(name_to_value.keys()))!r}"
        )

    if transformed.__closure__ is None and transformed_freevar_name_set:
        raise RuntimeError("Transformed function closure is missing for captured names")

    if inspect.iscoroutinefunction(func):
        transformed_async = cast(Callable[..., Awaitable[Any]], transformed)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await transformed_async(*args, **kwargs)

        async_wrapper.__nighthawk_transformed_function__ = transformed  # type: ignore[attr-defined]
        return cast(NaturalFunctionCallable, async_wrapper)  # type: ignore[return-value]

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return transformed(*args, **kwargs)

    wrapper.__nighthawk_transformed_function__ = transformed  # type: ignore[attr-defined]
    return cast(NaturalFunctionCallable, wrapper)  # type: ignore[return-value]


def get_transformed_function(function: Callable[..., object]) -> Callable[..., object]:
    """Find a Natural function's compiled body through conventional wrapper chains.

    Standard inspect.unwrap follows the original source. This accessor exposes the
    transformed body for inspection; normal execution should use the decorated callable
    to establish its runtime context. Invalid inputs and wrapper cycles raise TypeError.
    """
    current: object = function
    seen_identity_set: set[int] = set()
    while id(current) not in seen_identity_set:
        seen_identity_set.add(id(current))
        if isinstance(current, (staticmethod, classmethod)) or inspect.ismethod(current):
            current = current.__func__
            continue
        transformed = getattr(current, "__nighthawk_transformed_function__", None)
        if callable(transformed):
            return transformed
        current = getattr(current, "__wrapped__", None)
        if current is None:
            break
    raise TypeError("Expected a Natural function or a conventional wrapper around one")
