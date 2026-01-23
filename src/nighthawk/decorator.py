from __future__ import annotations

import inspect
import textwrap
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from .ast_transform import transform_function_source
from .runtime import Runtime
from .tools import call_scope

F = TypeVar("F", bound=Callable[..., Any])


class _RuntimeProxy:
    def run_block(self, natural_program: str, output_names: list[str], return_annotation: object, is_in_loop: bool) -> dict[str, object]:
        from .environment import get_environment

        environment = get_environment()
        runtime = Runtime.from_environment(environment)
        return runtime.run_block(natural_program, output_names, return_annotation, is_in_loop)


def fn(func: F | None = None) -> F:
    if func is None:
        return lambda f: fn(f)  # type: ignore[return-value]

    # Compile once at decoration time.
    lines, _ = inspect.getsourcelines(func)
    source = textwrap.dedent("".join(lines))

    # Strip decorators from the extracted function source to avoid re-decoration.
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

    globals_ns: dict[str, object] = dict(func.__globals__)
    globals_ns["__nighthawk_runtime__"] = _RuntimeProxy()

    # Execute compiled module to define transformed function.
    module_ns: dict[str, object] = {}
    exec(code, globals_ns, module_ns)

    transformed = module_ns.get(func.__name__)
    if not callable(transformed):
        raise RuntimeError("Transformed function not found after compilation")

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with call_scope():
            return transformed(*args, **kwargs)

    return cast(F, wrapper)  # type: ignore[return-value]
