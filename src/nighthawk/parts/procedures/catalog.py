"""Optional progressive discovery over a caller's explicitly retained procedures.

Catalog identities describe procedures; resolving one never executes source or
confers authority. Call signatures remain owned by the callable and Core renderer.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import SourceProvenanceClass
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.harness.proposals import DependencyDeclaration

__all__ = ["ProcedureCatalog", "ProcedureDescriptor", "ProcedurePage", "ProcedureQuery", "ProcedureSelection", "build_procedure_discovery"]


class ProcedureDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    procedure_id: str
    name: str
    purpose: str
    source_provenance_class: SourceProvenanceClass
    originating_run_id: str | None = None
    definition_id: str | None = None
    dependency_tuple: tuple[DependencyDeclaration, ...] = ()
    effect_footprint: EffectFootprint = EffectFootprint()


class ProcedureQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    starting_index: int = Field(default=0, ge=0)
    maximum_item_count: int = Field(default=20, gt=0)


class ProcedureSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    procedure_id: str | None
    reason: str = Field(max_length=2000)


class ProcedurePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    descriptor_tuple: tuple[ProcedureDescriptor, ...]
    next_starting_index: int | None
    incomplete: bool


class ProcedureCatalog:
    """Retain values by identity in registration order with finite metadata pages.

    Metadata bounds count the descriptor's JSON representation (type expressions
    use their ordinary string presentation). An oversized first descriptor makes
    a page unusable and raises ValueError; descriptors are never silently damaged.
    The caller may add current-run generated references after building discovery
    capabilities, so no capability identity needs to change during a run.
    """

    def __init__(self, *, maximum_metadata_character_count: int = 6000, maximum_page_item_count: int = 20) -> None:
        if maximum_metadata_character_count <= 0 or maximum_page_item_count <= 0:
            raise ValueError("Catalog page limits must be positive")
        self.maximum_metadata_character_count = maximum_metadata_character_count
        self.maximum_page_item_count = maximum_page_item_count
        self._procedure_id_to_value: dict[str, object] = {}
        self._procedure_id_to_descriptor: dict[str, ProcedureDescriptor] = {}

    @property
    def procedure_id_to_value(self) -> Mapping[str, object]:
        return MappingProxyType(self._procedure_id_to_value)

    def register(self, descriptor: ProcedureDescriptor, value: object) -> None:
        if descriptor.procedure_id in self._procedure_id_to_value:
            raise ValueError(f"Duplicate procedure identity: {descriptor.procedure_id}")
        self._procedure_id_to_value[descriptor.procedure_id] = value
        self._procedure_id_to_descriptor[descriptor.procedure_id] = descriptor

    def resolve(self, procedure_id: str) -> object:
        """Return the same retained value; unknown identities raise KeyError."""
        return self._procedure_id_to_value[procedure_id]

    def describe(self, procedure_id: str) -> ProcedureDescriptor:
        return self._procedure_id_to_descriptor[procedure_id]

    def query(self, request: ProcedureQuery) -> ProcedurePage:
        query = request.text.casefold()
        matching = tuple(
            descriptor
            for descriptor in self._procedure_id_to_descriptor.values()
            if query in descriptor.name.casefold() or query in descriptor.purpose.casefold()
        )
        descriptor_list: list[ProcedureDescriptor] = []
        character_count = 0
        for descriptor in matching[request.starting_index :]:
            size = len(descriptor.model_dump_json(fallback=str))
            if character_count + size > self.maximum_metadata_character_count or len(descriptor_list) >= min(
                request.maximum_item_count, self.maximum_page_item_count
            ):
                if not descriptor_list:
                    raise ValueError("Metadata bound cannot fit one procedure descriptor")
                break
            descriptor_list.append(descriptor)
            character_count += size
        next_index = request.starting_index + len(descriptor_list)
        incomplete = next_index < len(matching)
        return ProcedurePage(descriptor_tuple=tuple(descriptor_list), next_starting_index=next_index if incomplete else None, incomplete=incomplete)


def build_procedure_discovery(*, catalog: ProcedureCatalog) -> Grant:
    """Bind a query capability to one explicitly retained catalog before run entry."""

    def query_procedures(request: ProcedureQuery) -> ProcedurePage:
        """Find bounded procedure metadata by case-insensitive literal name or purpose."""
        return catalog.query(request)

    return Grant.of(
        Capability(name="query_procedures", function=query_procedures, authority_boundary="metadata from the caller-selected procedure catalog")
    )
