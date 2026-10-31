from __future__ import annotations

import asyncio
import inspect
from functools import wraps
from typing import Any, cast

import nighthawk as nh
from tests.support.executors import StubExecutor


def _transformed(function: object) -> Any:
    definition = nh.get_function_definition(function)
    assert definition is not None
    return definition.transformed_function


def test_original_unwrap_signature_and_transformed_body() -> None:
    def original(value: int = 3) -> int:
        """execute
        <value>
        {"step_outcome": {"kind": "return", "return_expression": "value + 1"}, "bindings": {}}
        """
        return 0

    decorated = nh.function(original)

    @wraps(decorated)
    def outer(*arguments: Any, **keywords: Any) -> object:
        return decorated(*arguments, **keywords)

    assert inspect.unwrap(decorated) is original
    assert inspect.unwrap(outer) is original
    assert inspect.signature(outer) == inspect.signature(original)
    transformed = _transformed(outer.__wrapped__)  # type: ignore[attr-defined]
    assert transformed is not original
    assert transformed.__globals__ is original.__globals__
    assert transformed.__name__ == original.__name__
    assert transformed.__qualname__ == original.__qualname__
    assert transformed.__code__.co_name == original.__code__.co_name
    assert transformed.__code__.co_qualname == original.__code__.co_qualname
    assert transformed.__doc__ == original.__doc__
    assert inspect.signature(transformed) == inspect.signature(original)
    with nh.run(StubExecutor()):
        assert outer(5) == 6


def test_async_and_method_descriptors() -> None:
    async def original() -> int:
        """execute
        {"step_outcome": {"kind": "return", "return_expression": "4"}, "bindings": {}}
        """
        return 0

    decorated = nh.function(original)
    assert inspect.unwrap(decorated) is original
    assert inspect.iscoroutinefunction(_transformed(decorated))

    class Container:
        method = cast(Any, decorated)
        static = staticmethod(decorated)
        class_method = classmethod(cast(Any, decorated))

    for function in (Container.static, Container.__dict__["static"], Container.class_method, Container.__dict__["class_method"], Container().method):
        assert _transformed(function) is _transformed(decorated)
    with nh.run(StubExecutor()):
        assert asyncio.run(decorated()) == 4
