"""Select in a small scope, then resolve and invoke under selected authority."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nighthawk.harness.context import current_grant, scope
from nighthawk.harness.dispatch import GeneratedCallableReference
from nighthawk.harness.grants import Grant
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.run_contract import RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.parts.procedures import materialize_procedure, select_procedure, selected_procedure
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureSelection


class DiscoveryInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    objective: str
    value_tuple: tuple[int, ...]


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    selection: ProcedureSelection
    proposal: CodeProposal
    result_tuple: tuple[int, ...]


def build_procedure_discovery_composition(
    *, catalog: ProcedureCatalog, run_entry_grant: Grant
) -> ScaffoldComposition[DiscoveryInput, DiscoveryResult]:
    def body(run_input: DiscoveryInput) -> DiscoveryResult:
        with scope(current_grant().restrict(("query_procedures",))):
            selection = select_procedure(run_input.objective)
        with selected_procedure(catalog, selection) as selected:
            # Selection needs interpretation; this fixed adapter needs no further judgment.
            # Constructed source still uses the ordinary generated-code admission path.
            proposal = CodeProposal(
                source="def calculate(value: int) -> int:\n    return selected(value)\n",
                export_name_tuple=("calculate",),
                dependency_tuple=(DependencyDeclaration(name="selected", kind="reference", declared_type=object),),
            )
            with scope(current_grant().with_references(selected=selected)):
                generated_selection = materialize_procedure(catalog, proposal, export_name="calculate", purpose=run_input.objective)
                with selected_procedure(catalog, generated_selection) as generated:
                    assert isinstance(generated, GeneratedCallableReference)
                    results = tuple(generated(value) for value in run_input.value_tuple)
        return DiscoveryResult.model_validate({"selection": selection, "proposal": proposal, "result_tuple": results})

    return ScaffoldComposition(
        run_contract=RunContract(input_type=DiscoveryInput, result_type=DiscoveryResult),
        body=body,
        run_entry_grant=run_entry_grant,
        composition_perimeter_policy=PerimeterPolicy.NONE,
        function_definition_tuple=(select_procedure,),
    )


def run_procedure_discovery(
    run_input: DiscoveryInput,
    *,
    catalog: ProcedureCatalog,
    runner: Runner,
    run_entry_grant: Grant,
    handle: RunHandle | None = None,
) -> DiscoveryResult:
    return runner.run(build_procedure_discovery_composition(catalog=catalog, run_entry_grant=run_entry_grant), run_input, handle=handle)
