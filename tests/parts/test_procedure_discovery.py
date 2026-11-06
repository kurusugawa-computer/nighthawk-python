from dataclasses import replace

import pytest

from examples.procedure_discovery import DiscoveryInput, run_procedure_discovery, selected_procedure
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import CallbackExecutor
from nighthawk.harness.context import current_capabilities, current_grant, scope
from nighthawk.harness.errors import DispatchRejectedError
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import DispatchEvent
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.run_handle import RunHandle
from nighthawk.parts.procedures.catalog import ProcedureCatalog, ProcedureDescriptor, ProcedureQuery, ProcedureSelection, build_procedure_discovery
from tests.support.harness import make_runner
from tests.support.procedures import make_catalog, square
from tests.support.runs import EchoInput, EchoResult, run_body


def test_selected_callable_is_wrapped_without_an_extra_judgment_and_reused() -> None:
    catalog = make_catalog()
    seen = []

    def handler(call: StepRequest) -> StepCandidate:
        seen.append(set(current_capabilities()))
        assert "selection" in call.output_binding_names
        assert call.step_context.environment.python_name_to_value["ProcedureQuery"] is ProcedureQuery
        assert call.step_context.environment.python_name_to_value["ProcedureSelection"] is ProcedureSelection
        return StepCandidate(binding_name_to_value={"selection": ProcedureSelection(procedure_id="square", reason="Matches the task")})

    handle = RunHandle()
    result = run_procedure_discovery(
        DiscoveryInput(objective="Square integers", value_tuple=(3, 7)),
        catalog=catalog,
        run_entry_grant=build_procedure_discovery(catalog=catalog),
        handle=handle,
        runner=replace(make_runner(), step_executor=CallbackExecutor(handler)),
    )
    assert result.result_tuple == (9, 49)
    assert seen == [{"query_procedures"}]
    invocations = [
        entry.event for entry in handle.ledger.entries if isinstance(entry.event, DispatchEvent) and entry.event.dispatch_kind == "invocation"
    ]
    assert len(invocations) == 2 and invocations[0].definition_id == invocations[1].definition_id
    stale = catalog.resolve(invocations[0].definition_id)  # type: ignore[arg-type]
    with pytest.raises(DispatchRejectedError):
        stale(3)  # type: ignore[operator]


def test_selected_capability_checks_actual_enclosing_authority_and_restores_scope() -> None:
    capability = Capability(name="square", function=square, authority_boundary="integer arithmetic")
    catalog = ProcedureCatalog()
    catalog.register(ProcedureDescriptor(procedure_id="square", name="square", purpose="Square", source_provenance_class="ratified"), capability)
    selection = ProcedureSelection(procedure_id="square", reason="Selected")
    grant = Grant.of(capability)

    def body(run_input: EchoInput) -> EchoResult:
        with selected_procedure(catalog, selection) as function:
            assert function(5) == 25  # type: ignore[operator]
        assert current_grant().capabilities["square"] is capability
        with scope(Grant.EMPTY), pytest.raises(DispatchRejectedError), selected_procedure(catalog, selection):
            pytest.fail("A stale selection regained capability authority")
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=grant)


def test_missing_selection_is_distinct_from_an_explicit_no_match() -> None:
    from nighthawk.parts.procedures import select_procedure

    def body(run_input: EchoInput) -> EchoResult:
        with pytest.raises(ValueError, match="explicit selection"):
            select_procedure("No answer was supplied")
        return EchoResult()

    run_body(body, runner=make_runner(), step_executor=CallbackExecutor(lambda call: StepCandidate(binding_name_to_value={})))


def test_same_name_generated_definitions_and_missing_retained_dependencies() -> None:
    from nighthawk.parts.procedures import materialize_procedure

    catalog = ProcedureCatalog()
    proposal = CodeProposal(
        source="def calculate(value: int) -> int:\n    return value + offset\n",
        export_name_tuple=("calculate",),
        dependency_tuple=(DependencyDeclaration(name="offset", kind="reference", declared_type=int),),
    )

    def body(run_input: EchoInput) -> EchoResult:
        first = materialize_procedure(catalog, proposal, export_name="calculate", purpose="Add an offset")
        second = materialize_procedure(catalog, proposal, export_name="calculate", purpose="Add an offset")
        assert first.procedure_id != second.procedure_id
        assert catalog.describe(first.procedure_id).name == catalog.describe(second.procedure_id).name  # type: ignore[arg-type]
        with scope(Grant.EMPTY), pytest.raises(DispatchRejectedError), selected_procedure(catalog, first):
            pytest.fail("Missing dependency was not rejected")
        with selected_procedure(catalog, first) as function:
            assert function(40) == 42  # type: ignore[operator]
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(offset=2))


def test_discovery_exposes_the_query_without_an_extra_callable_prompt() -> None:
    from nighthawk.adapters.pydantic_ai.prompt import build_user_prompt

    catalog = make_catalog()
    prompts = []

    class RenderingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            prompts.append(
                "".join(
                    part
                    for part in build_user_prompt(
                        processed_natural_program=processed_natural_program, step_context=step_context, configuration=StepExecutorConfiguration()
                    )
                    if isinstance(part, str)
                )
            )
            assert "selection" in request.output_binding_names
            selected = ProcedureSelection(procedure_id="square", reason="Selected")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"selection": selected})

    result = run_procedure_discovery(
        DiscoveryInput(objective="Square an integer", value_tuple=(3,)),
        catalog=catalog,
        run_entry_grant=build_procedure_discovery(catalog=catalog),
        runner=replace(make_runner(), step_executor=RenderingExecutor()),
    )
    assert result.result_tuple == (9,)
    assert len(prompts) == 1
    assert "query_procedures" in prompts[0]
    assert "Return the integer's square" not in prompts[0]
