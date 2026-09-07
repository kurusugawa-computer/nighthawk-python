from __future__ import annotations

import asyncio
import inspect
from functools import wraps

import pytest

import nighthawk as nh
from tests.execution.stub_executor import StubExecutor


def test_original_unwrap_signature_and_transformed_accessor() -> None:
    def original(value: int = 3) -> int:
        """natural
        <value>
        {"step_outcome": {"kind": "return", "return_expression": "value + 1"}, "bindings": {}}
        """
        return 0

    decorated = nh.natural_function(original)

    @wraps(decorated)
    def outer(*arguments: object, **keywords: object) -> object:
        return decorated(*arguments, **keywords)

    assert inspect.unwrap(decorated) is original
    assert inspect.unwrap(outer) is original
    assert inspect.signature(outer) == inspect.signature(original)
    transformed = nh.get_transformed_function(outer)
    assert transformed is not original
    assert transformed.__globals__ is original.__globals__
    with nh.run(StubExecutor()):
        assert outer(5) == 6


def test_async_and_method_descriptors() -> None:
    async def original() -> int:
        """natural
        {"step_outcome": {"kind": "return", "return_expression": "4"}, "bindings": {}}
        """
        return 0

    decorated = nh.natural_function(original)
    assert inspect.unwrap(decorated) is original
    assert inspect.iscoroutinefunction(nh.get_transformed_function(decorated))

    class Container:
        method = decorated
        static = staticmethod(decorated)
        class_method = classmethod(decorated)

    for function in (Container.static, Container.__dict__["static"], Container.class_method, Container.__dict__["class_method"], Container().method):
        assert nh.get_transformed_function(function) is nh.get_transformed_function(decorated)
    with nh.run(StubExecutor()):
        assert asyncio.run(decorated()) == 4


def test_invalid_inputs_and_cycles() -> None:
    def ordinary() -> None:
        pass

    for function in (ordinary, None, 1):
        with pytest.raises(TypeError):
            nh.get_transformed_function(function)  # type: ignore[arg-type]
    ordinary.__wrapped__ = ordinary  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        nh.get_transformed_function(ordinary)
