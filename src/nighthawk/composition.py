"""Explicit inheritance and composition operations for execution scopes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar


class UnsetType:
    """The singleton type of :data:`UNSET`, representing an omitted argument."""

    __slots__ = ()
    _instance: ClassVar[UnsetType | None] = None

    def __new__(cls) -> UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __copy__(self) -> UnsetType:
        return self

    def __deepcopy__(self, identity_to_copy: dict[int, object]) -> UnsetType:
        return self


UNSET = UnsetType()
"""Inherit the enclosing scope value; also marks omitted rewrite fields."""


@dataclass(frozen=True, init=False)
class Extend[T]:
    """Append a sequence to an inherited ordered collection, preserving entry identity."""

    values: tuple[T, ...]

    def __init__(self, values: Sequence[T]) -> None:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            raise TypeError("Extend requires a sequence of entries")
        object.__setattr__(self, "values", tuple(values))


@dataclass(frozen=True, init=False)
class Merge[T]:
    """Merge named references with inherited references, checking conflicts by identity."""

    name_to_value: Mapping[str, T]

    def __init__(self, name_to_value: Mapping[str, T]) -> None:
        if not isinstance(name_to_value, Mapping):
            raise TypeError("Merge requires a name-to-value mapping")
        if any(not isinstance(name, str) for name in name_to_value):
            raise TypeError("Merge names must be strings")
        object.__setattr__(self, "name_to_value", MappingProxyType(dict(name_to_value)))
