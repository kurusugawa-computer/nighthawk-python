"""Exact, non-owning registration of Core-created Natural wrappers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import MethodType
from weakref import ReferenceType
from weakref import ref as weak_reference


@dataclass(frozen=True, slots=True, weakref_slot=True)
class NaturalFunctionDefinition:
    """A snapshot retaining the actual source, public wrapper, and compiled body."""

    original_function: Callable[..., object]
    function: Callable[..., object]
    transformed_function: Callable[..., object]


_function_id_to_definition_reference: dict[int, ReferenceType[NaturalFunctionDefinition]] = {}


def _register_definition(definition: NaturalFunctionDefinition) -> None:
    function_id = id(definition.function)

    def remove(reference: ReferenceType[NaturalFunctionDefinition]) -> None:
        if _function_id_to_definition_reference.get(function_id) is reference:
            del _function_id_to_definition_reference[function_id]

    _function_id_to_definition_reference[function_id] = weak_reference(definition, remove)


def get_natural_function_definition(function: object) -> NaturalFunctionDefinition | None:
    """Look up an exact wrapper, normalizing only standard method descriptors.

    Copied attributes and unrelated outer decorators are not registration evidence.
    The registry does not keep an abandoned function namespace alive, but a returned
    snapshot intentionally retains its three callable objects.
    """
    if isinstance(function, (MethodType, staticmethod, classmethod)):
        function = function.__func__
    reference = _function_id_to_definition_reference.get(id(function))
    definition = reference() if reference is not None else None
    return definition if definition is not None and definition.function is function else None
