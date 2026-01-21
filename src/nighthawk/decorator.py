from __future__ import annotations

import inspect
import textwrap
from functools import wraps
from typing import Any, Callable, TypeVar

from .ast_transform import transform_function_source
from .configuration import Configuration
from .runtime import Runtime

F = TypeVar("F", bound=Callable[..., Any])


def fn(func: F | None = None, *, configuration: Configuration | None = None) -> F:
    if func is None:
        return lambda f: fn(f, configuration=configuration)  # type: ignore[return-value]

    config = configuration or Configuration()

    # Compile once at decoration time.
    lines, _lineno = inspect.getsourcelines(func)
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

    def _make_runtime() -> Runtime:
        return Runtime.from_configuration(config)

    globals_ns: dict[str, object] = dict(func.__globals__)
    globals_ns["__nighthawk_runtime__"] = _make_runtime()

    # Execute compiled module to define transformed function.
    module_ns: dict[str, object] = {}
    exec(code, globals_ns, module_ns)

    transformed = module_ns.get(func.__name__)
    if not callable(transformed):
        raise RuntimeError("Transformed function not found after compilation")

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return transformed(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
