"""Capabilities and grants.

A capability is a typed reference through which interior execution requests a
host-mediated operation. A grant is an immutable mapping from names to
capabilities and plain references that bounds what one execution may reach.
Grant composition helpers are ordinary Python conveniences; they confer no
authority beyond the resulting validated grant.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, Literal

from ._perimeter import EffectFootprint, PerimeterEffectClass
from .errors import GrantError

__all__ = [
    "Capability",
    "Grant",
    "GrantEntryKind",
]


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(name: str, *, role: str) -> None:
    if not _IDENTIFIER_PATTERN.fullmatch(name):
        raise ValueError(f"{role} must be an ASCII identifier, got {name!r}")


def signature_text(function: Callable[..., object]) -> str:
    """Return the call signature with string annotations resolved when possible."""

    try:
        return str(inspect.signature(function, eval_str=True))
    except (NameError, TypeError, ValueError):
        try:
            return str(inspect.signature(function))
        except (TypeError, ValueError):
            return "(...)"


def is_async_callable(function: object) -> bool:
    if inspect.iscoroutinefunction(function):
        return True
    call_attribute = getattr(function, "__call__", None)  # noqa: B004
    return call_attribute is not None and inspect.iscoroutinefunction(call_attribute)


@dataclass(frozen=True, eq=False)
class Capability:
    """A typed, host-mediated operation with a stable identity.

    ``name`` is the stable identity used in grants and the run ledger,
    ``function`` is the typed Python callable that performs the operation,
    ``authority_boundary`` describes the limit of what the operation can reach
    independently of who invokes it, and ``effect_footprint`` declares its
    effects including perimeter classes. Identity is object identity: two
    ``Capability`` values are never equal unless they are the same object.

    Invocation validates supplied arguments and results, including Annotated constraints.
    Ordinary application classes use instance checks that preserve identity without validating their internals.
    Explicit annotations that cannot be resolved or validated fail rather than becoming unconstrained.
    Missing annotations, object, and Any are unconstrained; author-owned defaults retain Python identity and state.
    """

    name: str
    function: Callable[..., object]
    authority_boundary: str
    effect_footprint: EffectFootprint = field(default_factory=EffectFootprint)

    def __post_init__(self) -> None:
        validate_identifier(self.name, role="Capability.name")
        if not callable(self.function):
            raise TypeError("Capability.function must be callable")
        if not self.authority_boundary.strip():
            raise ValueError("Capability.authority_boundary must not be empty")

    @property
    def declared_type(self) -> str:
        """The declared call signature presented to Natural execution."""

        return signature_text(self.function)

    @property
    def is_async(self) -> bool:
        return is_async_callable(self.function)


type GrantEntryKind = Literal["capability", "reference"]


class Grant(Mapping[str, object]):
    """Immutable name-to-reference mapping bounding one execution's reach.

    Capabilities are projected into Natural prompts by name, declared type, and
    authority boundary. Plain references resolve declared dependencies of
    generated definitions and are never projected. Composition rejects a name
    bound to two different objects with ``GrantError(reason="ambiguous_name")``.
    """

    EMPTY: ClassVar[Grant]

    def __init__(
        self,
        capabilities: Iterable[Capability] = (),
        references: Mapping[str, object] | None = None,
    ) -> None:
        name_to_capability: dict[str, Capability] = {}
        for capability in capabilities:
            if not isinstance(capability, Capability):
                raise GrantError(reason="invalid_entry", detail=f"{type(capability).__name__} is not a Capability")
            existing = name_to_capability.get(capability.name)
            if existing is not None and existing is not capability:
                raise GrantError(reason="ambiguous_name", name_tuple=(capability.name,))
            name_to_capability[capability.name] = capability
        name_to_reference: dict[str, object] = {}
        for name, value in (references or {}).items():
            if not name.isidentifier():
                raise ValueError(f"Grant reference name must be a Python identifier, got {name!r}")
            if isinstance(value, Capability):
                raise GrantError(reason="invalid_entry", name_tuple=(name,), detail="pass capabilities through the capabilities argument")
            if name in name_to_capability:
                raise GrantError(reason="ambiguous_name", name_tuple=(name,))
            name_to_reference[name] = value
        self._name_to_capability: Mapping[str, Capability] = MappingProxyType(name_to_capability)
        self._name_to_reference: Mapping[str, object] = MappingProxyType(name_to_reference)

    @classmethod
    def of(cls, *capabilities: Capability, **name_to_reference: object) -> Grant:
        return cls(capabilities, name_to_reference)

    @classmethod
    def compose(cls, *grants: Grant) -> Grant:
        """Merge grants; a name bound to two different objects is ambiguous."""

        name_to_capability: dict[str, Capability] = {}
        name_to_reference: dict[str, object] = {}
        for grant in grants:
            for name, capability in grant.capabilities.items():
                existing_capability = name_to_capability.get(name)
                if name in name_to_reference or (existing_capability is not None and existing_capability is not capability):
                    raise GrantError(reason="ambiguous_name", name_tuple=(name,))
                name_to_capability[name] = capability
            for name, value in grant.references.items():
                if name in name_to_capability or (name in name_to_reference and name_to_reference[name] is not value):
                    raise GrantError(reason="ambiguous_name", name_tuple=(name,))
                name_to_reference[name] = value
        return cls(name_to_capability.values(), name_to_reference)

    @property
    def capabilities(self) -> Mapping[str, Capability]:
        return self._name_to_capability

    @property
    def references(self) -> Mapping[str, object]:
        return self._name_to_reference

    @property
    def perimeter_effect_class_set(self) -> frozenset[PerimeterEffectClass]:
        """Perimeter classes reachable through the capabilities of this grant."""

        reachable: set[PerimeterEffectClass] = set()
        for capability in self._name_to_capability.values():
            reachable.update(capability.effect_footprint.perimeter_effect_class_set)
        return frozenset(reachable)

    def entry_kind(self, name: str) -> GrantEntryKind | None:
        if name in self._name_to_capability:
            return "capability"
        if name in self._name_to_reference:
            return "reference"
        return None

    def restrict(self, names: Iterable[str]) -> Grant:
        """Keep only the named entries."""

        name_set = set(names)
        return Grant(
            (capability for name, capability in self._name_to_capability.items() if name in name_set),
            {name: value for name, value in self._name_to_reference.items() if name in name_set},
        )

    def without(self, names: Iterable[str]) -> Grant:
        """Drop the named entries."""

        name_set = set(names)
        return Grant(
            (capability for name, capability in self._name_to_capability.items() if name not in name_set),
            {name: value for name, value in self._name_to_reference.items() if name not in name_set},
        )

    def with_references(self, **name_to_reference: object) -> Grant:
        """Return a grant with additional plain references."""

        return Grant.compose(self, Grant((), name_to_reference))

    def widening_over(self, enclosing: Grant) -> tuple[str, ...]:
        """Return capability names this grant holds beyond ``enclosing``.

        A capability counts as held by ``enclosing`` only when the same object is
        bound under the same name there.
        """

        return tuple(sorted(name for name, capability in self._name_to_capability.items() if enclosing.capabilities.get(name) is not capability))

    def __getitem__(self, name: str) -> object:
        if name in self._name_to_capability:
            return self._name_to_capability[name]
        return self._name_to_reference[name]

    def __iter__(self) -> Iterator[str]:
        yield from self._name_to_capability
        yield from self._name_to_reference

    def __len__(self) -> int:
        return len(self._name_to_capability) + len(self._name_to_reference)

    def __repr__(self) -> str:
        return f"Grant(capabilities={sorted(self._name_to_capability)}, references={sorted(self._name_to_reference)})"


Grant.EMPTY = Grant()
