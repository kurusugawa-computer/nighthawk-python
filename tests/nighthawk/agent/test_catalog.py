"""Procedure lookup has bounded metadata and ordinary retained identities."""

import pytest

from nighthawk.agent.catalog import ProcedureCatalog, ProcedureDescriptor, ProcedureQuery, build_procedure_discovery


def _descriptor(identity: str, *, name: str = "negative", purpose: str = "Find negative values") -> ProcedureDescriptor:
    return ProcedureDescriptor(procedure_id=identity, name=name, purpose=purpose, source_provenance_class="ratified")


def test_catalog_pages_preserve_registration_order_and_duplicate_names() -> None:
    catalog = ProcedureCatalog(maximum_page_item_count=1)
    first, second = object(), object()
    catalog.register(_descriptor("one"), first)
    catalog.register(_descriptor("two"), second)
    page = catalog.query(ProcedureQuery(text="NEGATIVE"))
    assert [descriptor.procedure_id for descriptor in page.descriptor_tuple] == ["one"]
    assert page.incomplete and page.next_starting_index == 1
    final = catalog.query(ProcedureQuery(text="negative", starting_index=1))
    assert final.descriptor_tuple[0].procedure_id == "two" and not final.incomplete
    assert catalog.resolve("one") is first and catalog.resolve("two") is second
    assert catalog.query(ProcedureQuery(starting_index=20)).descriptor_tuple == ()
    assert catalog.query(ProcedureQuery(text=".*")).descriptor_tuple == ()
    with pytest.raises(ValueError, match="Duplicate"):
        catalog.register(_descriptor("one"), second)
    with pytest.raises(KeyError):
        catalog.resolve("missing")


def test_metadata_bounds_are_enforced_without_silent_descriptor_truncation() -> None:
    descriptor = _descriptor("one")
    size = len(descriptor.model_dump_json())
    catalog = ProcedureCatalog(maximum_metadata_character_count=size)
    catalog.register(descriptor, object())
    catalog.register(_descriptor("two"), object())
    assert len(catalog.query(ProcedureQuery()).descriptor_tuple) == 1
    unusable = ProcedureCatalog(maximum_metadata_character_count=1)
    unusable.register(descriptor, object())
    with pytest.raises(ValueError, match="fit"):
        unusable.query(ProcedureQuery())
    with pytest.raises(ValueError):
        ProcedureQuery(maximum_item_count=0)
    with pytest.raises(ValueError):
        ProcedureCatalog(maximum_metadata_character_count=0)


def test_discovery_capability_keeps_identity_when_catalog_is_populated_later() -> None:
    catalog = ProcedureCatalog()
    grant = build_procedure_discovery(catalog=catalog)
    query = grant.capabilities["query_procedures"]
    assert query.function(ProcedureQuery()).descriptor_tuple == ()  # type: ignore[attr-defined]
    value = object()
    catalog.register(_descriptor("later"), value)
    assert query.function(ProcedureQuery()).descriptor_tuple[0].procedure_id == "later"  # type: ignore[attr-defined]
    assert catalog.resolve("later") is value
