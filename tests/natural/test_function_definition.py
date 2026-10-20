from __future__ import annotations

import gc
import inspect
import weakref
from functools import wraps

import nighthawk as nh


def test_exact_correspondence_and_descriptors() -> None:
    def original(self: object = None) -> object:
        return self

    function = nh.function(original)
    definition = nh.get_function_definition(function)
    assert definition is not None
    assert definition.original_function is original
    assert definition.function is function
    assert definition.transformed_function is nh.get_transformed_function(function)
    assert inspect.signature(function) == inspect.signature(original)

    @wraps(function)
    def outer(*arguments: object, **keywords: object) -> object:
        return function(*arguments, **keywords)

    class Container:
        method = function
        static = staticmethod(function)
        class_method = classmethod(function)

    for descriptor in (
        Container().method,
        Container.static,
        Container.class_method,
        Container.__dict__["static"],
        Container.__dict__["class_method"],
    ):
        assert nh.get_function_definition(descriptor) is definition
    for other in (original, definition.transformed_function, outer, None, [], 3):
        assert nh.get_function_definition(other) is None
    assert nh.get_transformed_function(outer) is definition.transformed_function


def test_registry_does_not_retain_abandoned_namespace() -> None:
    def original() -> None:
        pass

    # Source stays inspectable while globals deliberately reference the wrapper.
    from types import FunctionType

    namespace: dict[str, object] = {"__name__": __name__}
    original = FunctionType(original.__code__, namespace)
    function = nh.function(original)
    namespace["function"] = function
    definition = nh.get_function_definition(function)
    assert definition is not None
    reference = weakref.ref(function)
    del namespace, function, original
    gc.collect()
    assert reference() is not None
    del definition
    gc.collect()
    assert reference() is None


def test_redefinition_is_distinct() -> None:
    def original() -> None:
        pass

    first = nh.function(original)
    second = nh.function(original)
    assert nh.get_function_definition(first) != nh.get_function_definition(second)
