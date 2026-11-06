"""Grants are explicit immutable mappings; composition rejects ambiguity."""

from __future__ import annotations

import pytest

from nighthawk.agent import Capability, EffectFootprint, Grant, GrantError


def _lookup(key: str) -> str:
    """Look up a key."""

    return key


def _other(key: str) -> str:
    return key


def test_capability_requires_identifier_name_callable_and_boundary() -> None:
    with pytest.raises(ValueError):
        Capability(name="not valid", function=_lookup, authority_boundary="x")
    with pytest.raises(TypeError):
        Capability(name="lookup", function="not callable", authority_boundary="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Capability(name="lookup", function=_lookup, authority_boundary="   ")


def test_capability_declared_type_is_the_signature_and_identity_is_object_identity() -> None:
    first = Capability(name="lookup", function=_lookup, authority_boundary="read-only")
    second = Capability(name="lookup", function=_lookup, authority_boundary="read-only")
    assert first.declared_type == "(key: str) -> str"
    assert first != second
    assert hash(first) != hash(second) or first is not second


def test_grant_rejects_two_different_capabilities_under_one_name() -> None:
    first = Capability(name="lookup", function=_lookup, authority_boundary="a")
    second = Capability(name="lookup", function=_other, authority_boundary="b")
    with pytest.raises(GrantError) as captured:
        Grant.of(first, second)
    assert captured.value.reason == "ambiguous_name"
    assert captured.value.name_tuple == ("lookup",)


def test_grant_rejects_reference_and_capability_name_collision() -> None:
    lookup = Capability(name="lookup", function=_lookup, authority_boundary="a")
    with pytest.raises(GrantError) as captured:
        Grant.of(lookup, lookup="value")
    assert captured.value.reason == "ambiguous_name"


def test_grant_rejects_capability_passed_as_reference() -> None:
    lookup = Capability(name="lookup", function=_lookup, authority_boundary="a")
    with pytest.raises(GrantError) as captured:
        Grant((), {"lookup": lookup})
    assert captured.value.reason == "invalid_entry"


def test_compose_accepts_identical_objects_and_rejects_conflicts() -> None:
    lookup = Capability(name="lookup", function=_lookup, authority_boundary="a")
    value = object()
    composed = Grant.compose(Grant.of(lookup, shared=value), Grant.of(lookup, shared=value, extra=1))
    assert composed.capabilities["lookup"] is lookup
    assert composed.references["shared"] is value
    assert composed.references["extra"] == 1
    with pytest.raises(GrantError) as captured:
        Grant.compose(Grant.of(shared=value), Grant.of(shared=object()))
    assert captured.value.reason == "ambiguous_name"


def test_restrict_without_and_with_references_are_ordinary_conveniences() -> None:
    lookup = Capability(name="lookup", function=_lookup, authority_boundary="a")
    other = Capability(name="other", function=_other, authority_boundary="b")
    grant = Grant.of(lookup, other, seed=1)
    assert set(grant.restrict(["lookup", "seed"])) == {"lookup", "seed"}
    assert set(grant.without(["other"])) == {"lookup", "seed"}
    assert grant.with_references(extra=2).references["extra"] == 2
    assert grant["lookup"] is lookup
    assert grant["seed"] == 1
    assert len(grant) == 3
    assert grant.entry_kind("lookup") == "capability"
    assert grant.entry_kind("seed") == "reference"
    assert grant.entry_kind("missing") is None


def test_widening_over_reports_capabilities_not_held_by_the_enclosing_grant() -> None:
    lookup = Capability(name="lookup", function=_lookup, authority_boundary="a")
    other = Capability(name="other", function=_other, authority_boundary="b")
    renamed = Capability(name="lookup", function=_lookup, authority_boundary="a")
    enclosing = Grant.of(lookup)
    assert Grant.of(lookup, seed=1).widening_over(enclosing) == ()
    assert Grant.of(lookup, other).widening_over(enclosing) == ("other",)
    assert Grant.of(renamed).widening_over(enclosing) == ("lookup",)


def test_perimeter_effect_class_set_is_the_union_of_capability_footprints() -> None:
    egress = Capability(
        name="fetch", function=_lookup, authority_boundary="a", effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress"}))
    )
    write = Capability(
        name="write",
        function=_other,
        authority_boundary="b",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"durable_write_back"})),
    )
    assert Grant.of(egress, write).perimeter_effect_class_set == frozenset({"egress", "durable_write_back"})
    assert Grant.EMPTY.perimeter_effect_class_set == frozenset()
