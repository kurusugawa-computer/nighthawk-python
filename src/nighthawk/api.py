from __future__ import annotations

import inspect
import textwrap
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from . import core as core_module
from . import tools as tools_module
from .dsl import transform_function_source
from .runtime import Runtime
from .tools import call_scope

Configuration = core_module.Configuration
Environment = core_module.Environment
environment = core_module.environment
environment_override = core_module.environment_override
get_environment = core_module.get_environment

tool = tools_module.tool
get_current_tool_context = tools_module.get_current_tool_context
get_tool_context_stack = tools_module.get_tool_context_stack

F = TypeVar("F", bound=Callable[..., Any])


class _RuntimeProxy:
    def run_block(
        self,
        natural_program: str,
        output_names: list[str],
        return_annotation: object,
        is_in_loop: bool,
    ) -> dict[str, object]:
        frame = inspect.currentframe()
        if frame is None or frame.f_back is None:
            raise RuntimeError("No caller frame")
        caller_frame = frame.f_back

        current_environment = get_environment()
        runtime = Runtime.from_environment(current_environment)
        return runtime.run_block(
            natural_program,
            output_names,
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
        mod = __import__("ast").parse(source)
        for node in mod.body:
            if isinstance(node, __import__("ast").FunctionDef) and node.name == func.__name__:
                node.decorator_list = []
                source = __import__("ast").unparse(mod)
                break
    except Exception:
        pass

    transformed_source = transform_function_source(source)

    filename = inspect.getsourcefile(func) or "<nighthawk>"
    code = compile(transformed_source, filename, "exec")

    globals_namespace: dict[str, object] = dict(func.__globals__)
    globals_namespace["__nighthawk_runtime__"] = _RuntimeProxy()

    module_namespace: dict[str, object] = {}
    exec(code, globals_namespace, module_namespace)

    transformed = module_namespace.get(func.__name__)
    if not callable(transformed):
        raise RuntimeError("Transformed function not found after compilation")

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with call_scope():
            return transformed(*args, **kwargs)

    return cast(F, wrapper)  # type: ignore[return-value]
