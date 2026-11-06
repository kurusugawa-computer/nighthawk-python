"""Effect footprints and perimeter policies.

The perimeter is the set of irreversible effect classes: egress, destructive
operations, and durable write-back. Interior effects (reversible within a run)
are governed by rules and trace; only perimeter effects are prevented.
"""

from __future__ import annotations

from typing import ClassVar, Literal, get_args

from pydantic import BaseModel, ConfigDict

__all__ = [
    "PERIMETER_EFFECT_CLASS_TUPLE",
    "EffectFootprint",
    "PerimeterEffectClass",
    "PerimeterPolicy",
]


type PerimeterEffectClass = Literal["egress", "destructive", "durable_write_back"]

PERIMETER_EFFECT_CLASS_TUPLE: tuple[PerimeterEffectClass, ...] = get_args(PerimeterEffectClass.__value__)


class EffectFootprint(BaseModel):
    """Declared effects of one capability, code proposal, or generated definition.

    ``namespace_write_name_set`` names shared bindings the execution rebinds,
    ``mutable_reference_name_set`` names declared references it may mutate in place,
    ``capability_name_set`` names the capabilities it may request, and
    ``perimeter_effect_class_set`` lists the perimeter classes it may cause.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    NONE: ClassVar[EffectFootprint]

    namespace_write_name_set: frozenset[str] = frozenset()
    mutable_reference_name_set: frozenset[str] = frozenset()
    capability_name_set: frozenset[str] = frozenset()
    perimeter_effect_class_set: frozenset[PerimeterEffectClass] = frozenset()


class PerimeterPolicy(BaseModel):
    """The set of perimeter effect classes a policy permits.

    Policies compose by attenuation only: the effective policy of a run is the
    intersection of the platform perimeter policy and the composition perimeter
    policy. The default policy permits nothing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    NONE: ClassVar[PerimeterPolicy]

    permitted_effect_class_set: frozenset[PerimeterEffectClass] = frozenset()

    @classmethod
    def permitting(cls, *effect_classes: PerimeterEffectClass) -> PerimeterPolicy:
        return cls(permitted_effect_class_set=frozenset(effect_classes))

    @classmethod
    def permitting_all(cls) -> PerimeterPolicy:
        return cls(permitted_effect_class_set=frozenset(PERIMETER_EFFECT_CLASS_TUPLE))

    def permits(self, effect_class_set: frozenset[PerimeterEffectClass]) -> bool:
        return effect_class_set <= self.permitted_effect_class_set

    def excess_over(self, other: PerimeterPolicy) -> frozenset[PerimeterEffectClass]:
        """Return the classes this policy permits that ``other`` does not."""

        return self.permitted_effect_class_set - other.permitted_effect_class_set

    def intersection(self, other: PerimeterPolicy) -> PerimeterPolicy:
        return PerimeterPolicy(permitted_effect_class_set=self.permitted_effect_class_set & other.permitted_effect_class_set)


EffectFootprint.NONE = EffectFootprint()
PerimeterPolicy.NONE = PerimeterPolicy()
