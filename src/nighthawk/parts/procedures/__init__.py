"""Reusable discovery judgments and ordinary scoped procedure composition.

Catalogs remain caller-owned and optional. A selected description never grants authority;
invocation uses the enclosing run's grants and generated-source lifetime checks.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager

import nighthawk as nh
from nighthawk.harness.context import current_capabilities, current_grant, current_run_ledger, scope
from nighthawk.harness.errors import DispatchRejectedError
from nighthawk.harness.generated import RetainedFunction, retain_function
from nighthawk.harness.grants import Capability
from nighthawk.harness.proposals import CodeProposal
from nighthawk.parts.procedures.catalog import (
    ProcedureCatalog,
    ProcedureDescriptor,
    ProcedureQuery,  # noqa: F401 - explicit Natural read binding
    ProcedureSelection,
)

__all__ = ["materialize_procedure", "select_procedure", "selected_procedure"]


@nh.function
def select_procedure(objective: str) -> ProcedureSelection:
    """Choose an identity through granted discovery, without invoking or approving it."""
    selection: ProcedureSelection | None = None
    """execute
    ---
    deny: [raise, return, break, continue]
    ---
    Discover a useful procedure for <objective> with query_procedures(<ProcedureQuery>(text=...)).
    The query is a case-insensitive literal substring of a procedure's name or purpose,
    not a semantic search. Choose a short keyword, or use text="" to browse.
    Inspect the returned descriptor_tuple and follow next_starting_index if needed.
    Set <:selection> to <ProcedureSelection>(procedure_id=the selected identity, reason=a brief reason).
    Use procedure_id=None if no useful procedure is available. Metadata alone is not executable authority.
    Once a useful descriptor is found, assign the selection and finish without invoking it.
    """
    if selection is None:
        raise ValueError("Procedure discovery requires an explicit selection result")
    return selection


@contextmanager
def selected_procedure(
    catalog: ProcedureCatalog, selection: ProcedureSelection, *, reference_name_to_value: Mapping[str, object] | None = None
) -> Generator[object]:
    """Restore the caller's scope after invoking its retained selection.

    Dependencies and effects come from retained metadata. Capabilities must be
    the same objects held by the enclosing grant. Generated invocation performs
    the existing host dependency and lifetime checks again at its own seam.
    """
    if selection.procedure_id is None:
        raise ValueError("No procedure was selected")
    descriptor = catalog.describe(selection.procedure_id)
    value = catalog.resolve(selection.procedure_id)
    enclosing = current_grant()
    needed = set(descriptor.effect_footprint.capability_name_set)
    needed.update(dependency.name for dependency in descriptor.dependency_tuple if dependency.kind == "capability")
    if isinstance(value, Capability):
        needed.add(value.name)
        if enclosing.capabilities.get(value.name) is not value:
            raise DispatchRejectedError(reason="dependency_missing", detail=value.name)
    missing = needed - enclosing.capabilities.keys()
    if missing:
        raise DispatchRejectedError(reason="dependency_missing", detail=", ".join(sorted(missing)))
    resolved_reference_name_to_value = dict(reference_name_to_value or {})
    for dependency in descriptor.dependency_tuple:
        if dependency.kind == "reference" and dependency.name not in resolved_reference_name_to_value:
            if dependency.name not in enclosing.references:
                raise DispatchRejectedError(reason="dependency_missing", detail=dependency.name)
            resolved_reference_name_to_value[dependency.name] = enclosing.references[dependency.name]
    with scope(enclosing.restrict(needed).with_references(**resolved_reference_name_to_value)):
        if isinstance(value, Capability):
            yield current_capabilities()[value.name]
        elif isinstance(value, RetainedFunction):
            yield value.materialize()
        else:
            yield value


def materialize_procedure(catalog: ProcedureCatalog, proposal: CodeProposal, *, export_name: str, purpose: str) -> ProcedureSelection:
    """Materialize a definition-only proposal and explicitly retain one callable export.

    Retention survives runs without approving source or retaining invocation authority.
    Other exports are materialized by the same dispatch but are not registered here.
    """
    function = retain_function(proposal, export_name=export_name)
    definition = function.definition
    descriptor = ProcedureDescriptor(
        procedure_id=definition.definition_id,
        name=definition.export_name,
        purpose=purpose,
        source_provenance_class="generated",
        originating_run_id=current_run_ledger().run_id,
        definition_id=definition.definition_id,
        dependency_tuple=proposal.dependency_tuple,
        effect_footprint=proposal.effect_footprint,
    )
    catalog.register(descriptor, function)
    return ProcedureSelection(procedure_id=descriptor.procedure_id, reason="Materialized a typed proposal in the current run")
