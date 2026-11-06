from __future__ import annotations

import asyncio
import inspect
from collections.abc import Coroutine, Mapping
from typing import Any, cast

import pytest

import nighthawk as nh
from nighthawk.core.step_executor import StepExecutor
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import CallbackExecutor, ScriptedExecutor
from nighthawk.harness.confinement import unconfirmed_perimeter_confinement
from nighthawk.harness.context import current_capabilities, current_run_ledger, scope
from nighthawk.harness.dispatch import (
    AsyncGeneratedCallableReference,
    DispatchResult,
    GeneratedCallableReference,
    GeneratedDefinition,
    dispatch,
    execute_code_capability,
)
from nighthawk.harness.errors import DispatchRejectedError, GeneratedExecutionError
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import (
    DefinitionMaterializationEvent,
    DispatchEvent,
    DispatchOutcomeEvent,
    NaturalExecutionOutcomeEvent,
    ResourceExhaustionEvent,
    TraceAnomalyEvent,
)
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from tests.support.harness import ConfirmedConfinement, make_runner
from tests.support.runs import EchoInput, EchoResult, event_kinds, run_body

_ADD_SOURCE = "def add(a: int, b: int) -> int:\n    return a + b\n"


def _greet(name: str) -> str:
    """Greet someone."""

    return f"hello {name}"


def _greet_capability() -> Capability:
    return Capability(name="greet", function=_greet, authority_boundary="pure formatting")


def _egress_capability() -> Capability:
    return Capability(
        name="send",
        function=_greet,
        authority_boundary="egress",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress"})),
    )


def _run(body: object, *, grant: Grant = Grant.EMPTY, runner: Runner | None = None, step_executor: StepExecutor | None = None) -> RunHandle:
    handle = RunHandle()
    run_body(body, runner=runner if runner is not None else make_runner(), run_entry_grant=grant, handle=handle, step_executor=step_executor)  # type: ignore[arg-type]
    return handle


def test_definition_only_dispatch_materializes_typed_references_with_distinct_identities() -> None:
    results: list[DispatchResult] = []

    def body(run_input: EchoInput) -> EchoResult:
        proposal = CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",))
        results.append(dispatch(proposal))
        results.append(dispatch(proposal))
        add = results[0].export_name_to_value["add"]
        assert isinstance(add, GeneratedCallableReference)
        return EchoResult(value=str(add(2, 3)))

    handle = _run(body)
    assert results[0].definition_tuple[0].definition_id != results[1].definition_tuple[0].definition_id
    assert results[0].definition_tuple[0].defining_source_identity == results[1].definition_tuple[0].defining_source_identity
    assert results[0].definition_tuple[0].export_name == "add"
    kinds = event_kinds(handle.ledger.entries)
    assert kinds[1:5] == ["dispatch", "definition_materialization", "dispatch_outcome", "dispatch"]
    first_dispatch = handle.ledger.entries[1]
    materialization = handle.ledger.entries[2]
    assert materialization.parent_event_id == first_dispatch.event_id
    assert isinstance(materialization.event, DefinitionMaterializationEvent)
    assert materialization.event.source_provenance_class == "generated"
    assert materialization.event.materialization_origin_node_id == first_dispatch.event.dispatch_node_id  # type: ignore[union-attr]
    assert handle.ledger.definition(results[0].definition_tuple[0].definition_id) is materialization
    invocation_dispatches = [entry.event for entry in handle.ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation"]  # type: ignore[union-attr]
    assert len(invocation_dispatches) == 1
    assert invocation_dispatches[0].definition_id == results[0].definition_tuple[0].definition_id  # type: ignore[union-attr]
    assert invocation_dispatches[0].recursion_depth == 1  # type: ignore[union-attr]


def test_reference_invocation_validates_arguments_and_results_against_the_call_contract() -> None:
    observations: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        add = dispatch(CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",))).export_name_to_value["add"]
        observations.append(add("1", 2))  # type: ignore[operator]
        with pytest.raises(DispatchRejectedError) as rejected:
            add("x", 2)  # type: ignore[operator]
        observations.append(rejected.value.reason)
        bad = dispatch(CodeProposal(source="def bad() -> int:\n    return 'text'\n", export_name_tuple=("bad",))).export_name_to_value["bad"]
        with pytest.raises(GeneratedExecutionError) as contract:
            bad()  # type: ignore[operator]
        observations.append(contract.value.failure_kind)
        boom = dispatch(CodeProposal(source="def boom() -> int:\n    return 1 // 0\n", export_name_tuple=("boom",))).export_name_to_value["boom"]
        with pytest.raises(ZeroDivisionError):
            boom()  # type: ignore[operator]
        return EchoResult()

    handle = _run(body)
    assert observations == [3, "argument_contract_violation", "result_contract_violation"]
    outcome_statuses = [(entry.event.status, entry.event.failure_class_name) for entry in handle.ledger.select(DispatchOutcomeEvent)]  # type: ignore[union-attr]
    assert outcome_statuses == [
        ("success", None),
        ("success", None),
        ("success", None),
        ("failure", "GeneratedExecutionError"),
        ("success", None),
        ("failure", "ZeroDivisionError"),
    ]


def test_dependencies_resolve_from_the_grant_in_effect_at_invocation() -> None:
    greet = _greet_capability()
    grant = Grant.of(greet, suffix="!")
    reasons: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        shout = dispatch(
            CodeProposal(
                source="def shout(name: str) -> str:\n    return greet(name).upper() + suffix\n",
                export_name_tuple=("shout",),
                dependency_tuple=(
                    DependencyDeclaration(name="greet", kind="capability", declared_type="(name: str) -> str"),
                    DependencyDeclaration(name="suffix", kind="reference", declared_type=str),
                ),
                effect_footprint=EffectFootprint(capability_name_set=frozenset({"greet"})),
            )
        ).export_name_to_value["shout"]
        value = shout("bob")  # type: ignore[operator]
        with scope(grant.without(["greet"])), pytest.raises(DispatchRejectedError) as missing:
            shout("bob")  # type: ignore[operator]
        reasons.append(missing.value.reason)
        with scope(grant.without(["suffix"]).with_references(suffix=42)), pytest.raises(DispatchRejectedError) as mismatched:
            shout("bob")  # type: ignore[operator]
        reasons.append(mismatched.value.reason)
        with pytest.raises(DispatchRejectedError) as signature_mismatch:
            dispatch(
                CodeProposal(
                    source="def shout(name: str) -> str:\n    return greet(name, 1)\n",
                    export_name_tuple=("shout",),
                    dependency_tuple=(DependencyDeclaration(name="greet", kind="capability", declared_type="(name: str, extra: int) -> str"),),
                    effect_footprint=EffectFootprint(capability_name_set=frozenset({"greet"})),
                )
            )
        reasons.append(signature_mismatch.value.reason)
        return EchoResult(value=str(value))

    handle = _run(body, grant=grant)
    assert handle.ledger.entries[-1].event.terminal_kind == "accepted"  # type: ignore[union-attr]
    assert reasons == ["dependency_missing", "dependency_type_mismatch", "dependency_type_mismatch"]
    statuses = [entry.event.status for entry in handle.ledger.select(DispatchOutcomeEvent)]  # type: ignore[union-attr]
    assert statuses == ["success", "success"]


def test_dependency_kind_and_footprint_declarations_are_validated() -> None:
    greet = _greet_capability()
    send = _egress_capability()
    grant = Grant.of(greet, send, value=1)

    def reason(proposal: CodeProposal) -> str:
        with pytest.raises(DispatchRejectedError) as captured:
            dispatch(proposal)
        return captured.value.reason

    reasons: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        reasons.append(
            reason(
                CodeProposal(
                    source="def f() -> int:\n    return value\n",
                    export_name_tuple=("f",),
                    dependency_tuple=(DependencyDeclaration(name="value", kind="capability", declared_type="(x: int) -> int"),),
                    effect_footprint=EffectFootprint(capability_name_set=frozenset({"value"})),
                )
            )
        )
        reasons.append(
            reason(
                CodeProposal(
                    source="def f() -> str:\n    return greet('x')\n",
                    export_name_tuple=("f",),
                    dependency_tuple=(DependencyDeclaration(name="greet", kind="capability", declared_type="(name: str) -> str"),),
                )
            )
        )
        reasons.append(
            reason(
                CodeProposal(
                    source="def f() -> int:\n    return 1\n",
                    export_name_tuple=("f",),
                    effect_footprint=EffectFootprint(capability_name_set=frozenset({"unknown"})),
                )
            )
        )
        reasons.append(
            reason(
                CodeProposal(
                    source="def f() -> str:\n    return send('x')\n",
                    export_name_tuple=("f",),
                    dependency_tuple=(DependencyDeclaration(name="send", kind="capability", declared_type="(name: str) -> str"),),
                    effect_footprint=EffectFootprint(capability_name_set=frozenset({"send"})),
                )
            )
        )
        reasons.append(
            reason(
                CodeProposal(
                    source="def f() -> int:\n    return 1\n",
                    export_name_tuple=("f",),
                    effect_footprint=EffectFootprint(mutable_reference_name_set=frozenset({"value"})),
                )
            )
        )
        with scope(grant.without(["send"])):
            reasons.append(
                reason(
                    CodeProposal(
                        source="def f() -> int:\n    return 1\n",
                        export_name_tuple=("f",),
                        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress"})),
                    )
                )
            )
        return EchoResult()

    _run(body, grant=grant)
    assert reasons == [
        "dependency_kind_mismatch",
        "footprint_capability_undeclared",
        "capability_not_granted",
        "footprint_perimeter_undeclared",
        "footprint_mutable_reference_undeclared",
        "perimeter_outside_authority",
    ]


def test_argument_carried_capabilities_require_declaration_and_current_authority() -> None:
    greet = _greet_capability()
    grant = Grant.of(greet)
    observations: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        wrapper = current_capabilities()["greet"]
        apply = dispatch(
            CodeProposal(
                source="def apply(function, name: str) -> str:\n    return function(name)\n",
                export_name_tuple=("apply",),
                effect_footprint=EffectFootprint(capability_name_set=frozenset({"greet"})),
            )
        ).export_name_to_value["apply"]
        observations.append(apply(wrapper, "ann"))  # type: ignore[operator]
        undeclared = dispatch(
            CodeProposal(source="def apply(function, name: str) -> str:\n    return function(name)\n", export_name_tuple=("apply",))
        ).export_name_to_value["apply"]
        with pytest.raises(DispatchRejectedError) as captured:
            undeclared(wrapper, "ann")  # type: ignore[operator]
        observations.append(captured.value.reason)
        with pytest.raises(DispatchRejectedError) as raw:
            apply(greet, "ann")  # type: ignore[operator]
        observations.append(raw.value.reason)
        with scope(Grant.EMPTY), pytest.raises(DispatchRejectedError) as unauthorized:
            apply(wrapper, "ann")  # type: ignore[operator]
        observations.append(unauthorized.value.reason)
        return EchoResult()

    _run(body, grant=grant)
    assert observations == ["hello ann", "argument_capability_undeclared", "argument_capability_unauthorized", "capability_not_granted"]


def test_dispatch_is_rejected_before_execution_when_the_platform_cannot_confirm_confinement() -> None:
    runner = make_runner(confinement=None)
    runner = runner.__class__(
        step_executor=runner.step_executor,
        perimeter_policy=runner.perimeter_policy,
        resource_allocation_factory=runner.resource_allocation_factory,
        perimeter_confinement=unconfirmed_perimeter_confinement(),
        perimeter_audit_store=runner.perimeter_audit_store,
        accepts_aggregate_executor_accounting=True,
    )

    def body(run_input: EchoInput) -> EchoResult:
        with pytest.raises(DispatchRejectedError) as captured:
            dispatch(CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",)))
        return EchoResult(value=captured.value.reason)

    handle = RunHandle()
    result = run_body(body, runner=runner, handle=handle)
    assert result.value == "confinement_unavailable"
    assert handle.ledger.select(DispatchEvent) == ()


def test_confinement_is_confirmed_per_dispatch_with_the_declared_footprint() -> None:
    confinement = ConfirmedConfinement()
    runner = make_runner(confinement=confinement)

    def body(run_input: EchoInput) -> EchoResult:
        add = dispatch(CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",))).export_name_to_value["add"]
        add(1, 1)  # type: ignore[operator]
        return EchoResult()

    handle = _run(body, runner=runner)
    assert [request.recursion_depth for request in confinement.request_list] == [1, 1]
    assert {entry.event.confinement_identity for entry in handle.ledger.select(DispatchEvent)} == {"test-confinement"}  # type: ignore[union-attr]


def test_allocation_exhaustion_rejects_dispatch_with_a_recorded_event() -> None:
    runner = make_runner(allocation_factory=lambda run_identity: bounded_resource_allocation(maximum_recursion_depth=0))

    def body(run_input: EchoInput) -> EchoResult:
        with pytest.raises(DispatchRejectedError) as captured:
            dispatch(CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",)))
        assert captured.value.rejection_event_id is not None
        return EchoResult(value=captured.value.reason)

    handle = RunHandle()
    result = run_body(body, runner=runner, handle=handle)
    assert result.value == "allocation_exhausted"
    exhaustion = handle.ledger.select(ResourceExhaustionEvent)[0].event
    assert isinstance(exhaustion, ResourceExhaustionEvent)
    assert exhaustion.request_kind == "dispatch"
    assert exhaustion.recursion_depth == 1


def test_definition_and_invocation_dispatch_composes_materialization_with_a_mediated_invocation() -> None:
    results: list[DispatchResult] = []

    def body(run_input: EchoInput) -> EchoResult:
        result = dispatch(
            CodeProposal(
                source=_ADD_SOURCE,
                execution_intent="definition_and_invocation",
                export_name_tuple=("add",),
                entry_export_name="add",
                entry_argument_tuple=("4",),
                entry_keyword_argument_name_to_value={"b": 5},
            )
        )
        results.append(result)
        with pytest.raises(DispatchRejectedError) as rejected:
            dispatch(
                CodeProposal(
                    source=_ADD_SOURCE,
                    execution_intent="definition_and_invocation",
                    export_name_tuple=("add",),
                    entry_export_name="add",
                    entry_argument_tuple=("x", 1),
                )
            )
        assert rejected.value.reason == "argument_contract_violation"
        return EchoResult(value=str(result.entry_result))

    handle = _run(body)
    assert handle.ledger.entries[-1].event.terminal_kind == "accepted"  # type: ignore[union-attr]
    assert results[0].entry_result == 9
    dispatch_events = [entry.event for entry in handle.ledger.select(DispatchEvent)]
    assert [event.dispatch_kind for event in dispatch_events] == ["definition_and_invocation", "invocation", "definition_and_invocation"]  # type: ignore[union-attr]
    assert dispatch_events[1].parent_node_id == dispatch_events[0].parent_node_id  # type: ignore[union-attr]
    assert dispatch_events[1].definition_id == results[0].definition_tuple[0].definition_id  # type: ignore[union-attr]
    assert dispatch_events[1].dispatch_node_id == results[0].entry_dispatch_node_id  # type: ignore[union-attr]
    outcomes = [entry.event for entry in handle.ledger.select(DispatchOutcomeEvent)]
    assert [(outcome.status, outcome.result) for outcome in outcomes] == [("success", None), ("success", 9), ("success", None)]  # type: ignore[union-attr]
    assert outcomes[1].result_type_name == "builtins.int"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "annotation, valid_expression, invalid_expression, expected",
    [("int", "3", "'three'", 3), ("None", "None", "3", None)],
)
def test_value_exports_are_committed_and_validated_against_their_annotation(
    annotation: str, valid_expression: str, invalid_expression: str, expected: object
) -> None:
    values: list[object] = []
    reasons: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        result = dispatch(CodeProposal(source=f"limit: {annotation} = {valid_expression}\n", export_name_tuple=("limit",)))
        values.append(result.export_name_to_value["limit"])
        with pytest.raises(GeneratedExecutionError) as captured:
            dispatch(CodeProposal(source=f"limit: {annotation} = {invalid_expression}\n", export_name_tuple=("limit",)))
        reasons.append(captured.value.failure_kind)
        return EchoResult()

    _run(body)
    assert values == [expected]
    assert reasons == ["result_contract_violation"]


def test_execute_code_capability_enters_the_same_dispatch_path_from_a_natural_execution() -> None:
    seen_names: list[set[str]] = []
    results: list[object] = []

    def handler(call: StepRequest) -> StepCandidate:
        references = current_capabilities()
        seen_names.append(set(references))
        if "execute_code" in references:
            result = references["execute_code"](  # type: ignore[operator]
                "def double(x: int) -> int:\n    return x * 2\n",
                exports=["double"],
                intent="definition_and_invocation",
                entry_export="double",
                entry_arguments=[21],
            )
            results.append(result)
        return StepCandidate(binding_name_to_value={"outcome": "done"})

    @nh.function
    def act(text: str) -> str:
        outcome: str = ""
        """execute
        Read <text>; set <:outcome>.
        """
        return outcome

    grant = Grant.of(execute_code_capability())

    def body(run_input: EchoInput) -> EchoResult:
        act(run_input.text)
        with scope(Grant.EMPTY):
            act(run_input.text)
        return EchoResult()

    handle = _run(body, grant=grant, step_executor=CallbackExecutor(handler))
    assert seen_names == [{"execute_code"}, set()]
    assert len(results) == 1
    assert isinstance(results[0], DispatchResult)
    assert results[0].entry_result == 42
    dispatch_event = handle.ledger.select(DispatchEvent)[0].event
    assert isinstance(dispatch_event, DispatchEvent)
    assert dispatch_event.recursion_depth == 1


def test_execute_code_rejects_invalid_proposals_as_typed_rejections() -> None:
    reasons: list[str] = []

    def handler(call: StepRequest) -> StepCandidate:
        with pytest.raises(DispatchRejectedError) as captured:
            current_capabilities()["execute_code"]("def f(): pass", exports=[])  # type: ignore[operator]
        reasons.append(captured.value.reason)
        return StepCandidate(binding_name_to_value={"outcome": "done"})

    @nh.function
    def act(text: str) -> str:
        outcome: str = ""
        """execute
        Read <text>; set <:outcome>.
        """
        return outcome

    _run(lambda run_input: EchoResult(value=act(run_input.text)), grant=Grant.of(execute_code_capability()), step_executor=CallbackExecutor(handler))
    assert reasons == ["proposal_invalid"]


def test_generated_natural_functions_execute_under_the_dispatch_node() -> None:
    source = (
        "import nighthawk as nh\n\n"
        "@nh.function\n"
        "def classify(text: str) -> str:\n"
        '    label: str = ""\n'
        '    """execute\n'
        "    Read <text> and set <:label>.\n"
        '    """\n'
        "    return label\n"
    )
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"label": "generated-label"})])

    def body(run_input: EchoInput) -> EchoResult:
        classify = dispatch(CodeProposal(source=source, export_name_tuple=("classify",))).export_name_to_value["classify"]
        return EchoResult(value=classify(run_input.text))  # type: ignore[operator, arg-type]

    handle = _run(body, step_executor=executor)
    assert handle.ledger.entries[-1].event.result_type_name.endswith("EchoResult")  # type: ignore[union-attr]
    materialization = handle.ledger.select(DefinitionMaterializationEvent)[0].event
    assert isinstance(materialization, DefinitionMaterializationEvent)
    assert materialization.definition_kind == "function"
    invocation = next(entry.event for entry in handle.ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation")  # type: ignore[union-attr]
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.attribution_node_id == invocation.dispatch_node_id  # type: ignore[union-attr]
    assert executor.requests[0].step_context.step_locals["text"] == "input"


def test_generated_code_may_recurse_through_a_granted_execute_code_dependency() -> None:
    grant = Grant.of(execute_code_capability())
    source = (
        "def build_and_run() -> int:\n"
        "    result = execute_code('def inner() -> int:\\n    return 7\\n', exports=['inner'])\n"
        "    return result.export_name_to_value['inner']() * 2\n"
    )

    def body(run_input: EchoInput) -> EchoResult:
        build_and_run = dispatch(
            CodeProposal(
                source=source,
                export_name_tuple=("build_and_run",),
                dependency_tuple=(
                    DependencyDeclaration(name="execute_code", kind="capability", declared_type=execute_code_capability().declared_type),
                ),
                effect_footprint=EffectFootprint(capability_name_set=frozenset({"execute_code"})),
            )
        ).export_name_to_value["build_and_run"]
        return EchoResult(value=str(build_and_run()))  # type: ignore[operator]

    handle = _run(body, grant=grant)
    depths = [entry.event.recursion_depth for entry in handle.ledger.select(DispatchEvent)]  # type: ignore[union-attr]
    assert depths == [1, 1, 2, 2]
    assert handle.ledger.entries[-1].event.terminal_kind == "accepted"  # type: ignore[union-attr]


def test_reference_from_an_ended_or_foreign_run_is_rejected() -> None:
    retained: list[GeneratedCallableReference] = []

    def body(run_input: EchoInput) -> EchoResult:
        retained.append(dispatch(CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",))).export_name_to_value["add"])  # type: ignore[arg-type]
        return EchoResult()

    _run(body)
    with pytest.raises(DispatchRejectedError) as outside:
        retained[0](1, 2)
    assert outside.value.reason == "no_active_run"

    def second_body(run_input: EchoInput) -> EchoResult:
        with pytest.raises(DispatchRejectedError) as captured:
            retained[0](1, 2)
        return EchoResult(value=captured.value.reason)

    handle = RunHandle()
    assert run_body(second_body, runner=make_runner(), handle=handle).value == "definition_unavailable"


def test_interior_code_modification_is_a_trace_anomaly() -> None:
    def replacement(a: int, b: int) -> int:
        return a * b

    def body(run_input: EchoInput) -> EchoResult:
        add = dispatch(CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",))).export_name_to_value["add"]
        assert isinstance(add, GeneratedCallableReference)
        add._function.__code__ = replacement.__code__
        return EchoResult(value=str(add(2, 3)))

    handle = _run(body)
    anomaly = handle.ledger.select(TraceAnomalyEvent)[0].event
    assert isinstance(anomaly, TraceAnomalyEvent)
    assert anomaly.anomaly_kind == "definition_code_modified"
    assert handle.ledger.entries[-1].event.terminal_kind == "accepted"  # type: ignore[union-attr]


def test_source_rejections_surface_as_dispatch_rejections_without_a_dispatch_event() -> None:
    def body(run_input: EchoInput) -> EchoResult:
        with pytest.raises(DispatchRejectedError) as captured:
            dispatch(CodeProposal(source="def add():\n    return eval('1')\n", export_name_tuple=("add",)))
        return EchoResult(value=captured.value.reason)

    handle = RunHandle()
    assert run_body(body, runner=make_runner(), handle=handle).value == "raw_dynamic_execution"
    assert handle.ledger.select(DispatchEvent) == ()


_ASYNC_SOURCE = "async def double(x: int) -> int:\n    return x * 2\n"


def test_async_exports_are_mediated_when_awaited() -> None:
    observations: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        double = dispatch(CodeProposal(source=_ASYNC_SOURCE, export_name_tuple=("double",))).export_name_to_value["double"]
        assert isinstance(double, AsyncGeneratedCallableReference)
        assert double.is_async
        assert inspect.iscoroutinefunction(double.__call__)
        coroutine = double("21")
        assert len(current_run_ledger().select(DispatchEvent)) == 1
        observations.append(asyncio.run(coroutine))  # type: ignore[arg-type]
        with pytest.raises(DispatchRejectedError) as rejected:
            asyncio.run(double("x"))  # type: ignore[arg-type]
        observations.append(rejected.value.reason)
        bad = dispatch(CodeProposal(source="async def bad() -> int:\n    return 'text'\n", export_name_tuple=("bad",))).export_name_to_value["bad"]
        with pytest.raises(GeneratedExecutionError):
            asyncio.run(bad())  # type: ignore[arg-type, operator]
        boom = dispatch(CodeProposal(source="async def boom() -> int:\n    return 1 // 0\n", export_name_tuple=("boom",))).export_name_to_value[
            "boom"
        ]
        with pytest.raises(ZeroDivisionError):
            asyncio.run(boom())  # type: ignore[arg-type, operator]
        return EchoResult(value=str(observations[0]))

    handle = _run(body)
    assert observations == [42, "argument_contract_violation"]
    materialization = handle.ledger.select(DefinitionMaterializationEvent)[0].event
    assert isinstance(materialization, DefinitionMaterializationEvent)
    assert materialization.definition_kind == "async_function"
    invocation_dispatches = [entry.event for entry in handle.ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation"]  # type: ignore[union-attr]
    assert len(invocation_dispatches) == 3
    statuses = [(entry.event.status, entry.event.failure_class_name) for entry in handle.ledger.select(DispatchOutcomeEvent)]  # type: ignore[union-attr]
    assert statuses == [
        ("success", None),
        ("success", None),
        ("success", None),
        ("failure", "GeneratedExecutionError"),
        ("success", None),
        ("failure", "ZeroDivisionError"),
    ]


def test_async_generated_natural_functions_execute_under_the_dispatch_node() -> None:
    source = (
        "import nighthawk as nh\n\n"
        "@nh.function\n"
        "async def classify(text: str) -> str:\n"
        '    label: str = ""\n'
        '    """execute\n'
        "    Read <text> and set <:label>.\n"
        '    """\n'
        "    return label\n"
    )
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"label": "async-label"})])

    def body(run_input: EchoInput) -> EchoResult:
        classify = dispatch(CodeProposal(source=source, export_name_tuple=("classify",))).export_name_to_value["classify"]
        assert isinstance(classify, AsyncGeneratedCallableReference)
        return EchoResult(value=asyncio.run(classify(run_input.text)))  # type: ignore[arg-type]

    handle = _run(body, step_executor=executor)
    materialization = handle.ledger.select(DefinitionMaterializationEvent)[0].event
    assert isinstance(materialization, DefinitionMaterializationEvent)
    assert materialization.definition_kind == "async_function"
    invocation = next(entry.event for entry in handle.ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation")  # type: ignore[union-attr]
    outcome = handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
    assert isinstance(outcome, NaturalExecutionOutcomeEvent)
    assert outcome.attribution_node_id == invocation.dispatch_node_id  # type: ignore[union-attr]
    assert handle.ledger.entries[-1].event.terminal_kind == "accepted"  # type: ignore[union-attr]


def test_combined_intent_rejects_an_asynchronous_entry() -> None:
    def body(run_input: EchoInput) -> EchoResult:
        with pytest.raises(DispatchRejectedError) as captured:
            dispatch(
                CodeProposal(
                    source=_ASYNC_SOURCE,
                    execution_intent="definition_and_invocation",
                    export_name_tuple=("double",),
                    entry_export_name="double",
                    entry_argument_tuple=(1,),
                )
            )
        return EchoResult(value=captured.value.reason)

    handle = RunHandle()
    assert run_body(body, runner=make_runner(), handle=handle).value == "intent_mismatch"
    assert handle.ledger.select(DispatchEvent) == ()


_SIBLING_SOURCE = (
    "def inner(x: int) -> int:\n"
    "    return x + 1\n\n"
    "def outer(x: int) -> int:\n"
    "    return inner(x) * 10\n\n"
    "def pick():\n"
    "    return inner\n\n"
    "def helped(x: int) -> int:\n"
    "    return _helper(x)\n\n"
    "def leak_helper():\n"
    "    return [[[[_helper]]]]\n\n"
    "def leak_key():\n"
    "    return {_helper: 1}\n\n"
    "def leak_closure():\n"
    "    def made() -> int:\n"
    "        return 1\n"
    "    return made\n\n"
    "def leak_method():\n"
    "    return _Holder().method\n\n"
    "def leak_generator():\n"
    "    return _generate()\n\n"
    "def leak_lambda():\n"
    "    return lambda: 1\n\n"
    "def leak_generator_expression():\n"
    "    return (_helper(i) for i in range(3))\n\n"
    "def leak_coroutine():\n"
    "    return _co()\n\n"
    "def _helper(x: int = 0) -> int:\n"
    "    return x + 100\n\n"
    "def _generate():\n"
    "    yield 1\n"
    "    yield 2\n\n"
    "async def _co() -> int:\n"
    "    return 1\n\n"
    "class _Holder:\n"
    "    def method(self) -> int:\n"
    "        return 1\n"
)
_SIBLING_EXPORT_TUPLE = (
    "inner",
    "outer",
    "pick",
    "helped",
    "leak_helper",
    "leak_key",
    "leak_closure",
    "leak_method",
    "leak_generator",
    "leak_lambda",
    "leak_generator_expression",
    "leak_coroutine",
)


def test_sibling_exports_are_invoked_and_returned_through_their_host_wrappers() -> None:
    retained: list[object] = []
    observations: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        exports = dispatch(CodeProposal(source=_SIBLING_SOURCE, export_name_tuple=_SIBLING_EXPORT_TUPLE)).export_name_to_value
        observations.append(exports["outer"](4))  # type: ignore[operator]
        observations.append(exports["helped"](1))  # type: ignore[operator]
        picked = exports["pick"]()  # type: ignore[operator]
        observations.append(picked is exports["inner"])
        retained.append(picked)
        return EchoResult()

    handle = _run(body)
    assert observations == [50, 101, True]
    invocation_events = [entry.event for entry in handle.ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation"]  # type: ignore[union-attr]
    # outer, inner (called from outer's body), helped (its private helper runs inside the same node), pick.
    assert [event.export_name_tuple[0] for event in invocation_events] == ["outer", "inner", "helped", "pick"]  # type: ignore[union-attr]
    assert [event.recursion_depth for event in invocation_events] == [1, 2, 1, 1]  # type: ignore[union-attr]
    assert {entry.event.status for entry in handle.ledger.select(DispatchOutcomeEvent)} == {"success"}  # type: ignore[union-attr]
    with pytest.raises(DispatchRejectedError) as outside:
        retained[0](1)  # type: ignore[operator]
    assert outside.value.reason == "no_active_run"


def test_leaked_generated_code_cannot_start_or_resume_outside_its_dispatch() -> None:
    leaked: dict[str, object] = {}
    inside_reasons: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        exports = dispatch(CodeProposal(source=_SIBLING_SOURCE, export_name_tuple=_SIBLING_EXPORT_TUPLE)).export_name_to_value
        leaked["helper"] = exports["leak_helper"]()[0][0][0][0]  # type: ignore[index, operator]
        leaked["key"] = next(iter(exports["leak_key"]()))  # type: ignore[arg-type, operator]
        leaked["closure"] = exports["leak_closure"]()  # type: ignore[operator]
        leaked["method"] = exports["leak_method"]()  # type: ignore[operator]
        leaked["generator"] = exports["leak_generator"]()  # type: ignore[operator]
        leaked["lambda"] = exports["leak_lambda"]()  # type: ignore[operator]
        leaked["generator_expression"] = exports["leak_generator_expression"]()  # type: ignore[operator]
        leaked["coroutine"] = exports["leak_coroutine"]()  # type: ignore[operator]
        for name in ("helper", "key", "closure", "method", "lambda"):
            with pytest.raises(DispatchRejectedError) as captured:
                leaked[name]()  # type: ignore[operator]
            inside_reasons.append(captured.value.reason)
        with pytest.raises(DispatchRejectedError) as generator_captured:
            next(leaked["generator"])  # type: ignore[arg-type]
        inside_reasons.append(generator_captured.value.reason)
        with pytest.raises(DispatchRejectedError) as expression_captured:
            next(leaked["generator_expression"])  # type: ignore[arg-type]
        inside_reasons.append(expression_captured.value.reason)
        with pytest.raises(DispatchRejectedError) as coroutine_captured:
            asyncio.run(leaked["coroutine"])  # type: ignore[arg-type]
        inside_reasons.append(coroutine_captured.value.reason)
        return EchoResult()

    handle = _run(body)
    assert inside_reasons == ["unmediated_execution"] * 8
    anomalies = [entry.event for entry in handle.ledger.select(TraceAnomalyEvent)]
    assert {anomaly.anomaly_kind for anomaly in anomalies} == {"unmediated_generated_execution"}  # type: ignore[union-attr]
    assert len(anomalies) == 8
    for name in ("helper", "key", "closure", "method", "lambda"):
        with pytest.raises(DispatchRejectedError) as after_run:
            leaked[name]()  # type: ignore[operator]
        assert after_run.value.reason == "run_ended"
    assert handle.ledger.entries[-1].event.kind == "run_end"


def test_generated_source_may_not_reach_or_rebind_the_execution_guard() -> None:
    reasons: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        for source in (
            "def f() -> int:\n    globals()['__nighthawk_guard__'] = lambda: None\n    return 1\n",
            "def f() -> int:\n    f.__globals__['x'] = 1\n    return 1\n",
            "def f() -> int:\n    __nighthawk_guard__ = None\n    return 1\n",
            "def f(__nighthawk_guard__=None) -> int:\n    return 1\n",
            "def f() -> int:\n    return f.__code__.co_argcount\n",
        ):
            with pytest.raises(DispatchRejectedError) as captured:
                dispatch(CodeProposal(source=source, export_name_tuple=("f",)))
            reasons.append(captured.value.reason)
        return EchoResult()

    _run(body)
    assert reasons == ["introspection_not_allowed", "introspection_not_allowed", "reserved_name", "reserved_name", "introspection_not_allowed"]


def test_rebinding_the_guard_from_ratified_code_is_a_trace_anomaly_and_is_restored() -> None:
    def body(run_input: EchoInput) -> EchoResult:
        add = dispatch(CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",))).export_name_to_value["add"]
        assert isinstance(add, GeneratedCallableReference)
        add._source.namespace["__nighthawk_guard__"] = lambda: None
        return EchoResult(value=str(add(1, 2)))

    handle = _run(body)
    anomaly = handle.ledger.select(TraceAnomalyEvent)[0].event
    assert isinstance(anomaly, TraceAnomalyEvent)
    assert anomaly.anomaly_kind == "namespace_guard_modified"
    assert handle.ledger.entries[-1].event.terminal_kind == "accepted"  # type: ignore[union-attr]


def test_value_export_holding_a_generated_function_is_inert_outside_dispatch() -> None:
    def body(run_input: EchoInput) -> EchoResult:
        handler = dispatch(
            CodeProposal(source="def _helper() -> int:\n    return 1\n\nhandler = _helper\n", export_name_tuple=("handler",))
        ).export_name_to_value["handler"]
        with pytest.raises(DispatchRejectedError) as captured:
            handler()  # type: ignore[operator]
        return EchoResult(value=captured.value.reason)

    handle = RunHandle()
    assert run_body(body, runner=make_runner(), handle=handle).value == "unmediated_execution"


def test_generated_natural_function_calls_and_returns_siblings_through_host_wrappers() -> None:
    source = (
        "import nighthawk as nh\n\n"
        "def helper(x: int) -> int:\n"
        "    return x * 2\n\n"
        "@nh.function\n"
        "def classify(text: str) -> int:\n"
        '    label: str = ""\n'
        '    """execute\n'
        "    Read <text> and set <:label>.\n"
        '    """\n'
        "    return helper(len(label))\n\n"
        "@nh.function\n"
        "def pick(text: str):\n"
        '    label: str = ""\n'
        '    """execute\n'
        "    Read <text> and set <:label>.\n"
        '    """\n'
        "    return helper\n"
    )
    executor = ScriptedExecutor(
        responses=[StepCandidate(binding_name_to_value={"label": "abc"}), StepCandidate(binding_name_to_value={"label": "x"})]
    )
    observations: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        exports = dispatch(CodeProposal(source=source, export_name_tuple=("helper", "classify", "pick"))).export_name_to_value
        observations.append(exports["classify"](run_input.text))  # type: ignore[operator]
        observations.append(exports["pick"](run_input.text) is exports["helper"])  # type: ignore[operator]
        return EchoResult()

    handle = _run(body, step_executor=executor)
    assert observations == [6, True]
    invocation_events = [entry.event for entry in handle.ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation"]  # type: ignore[union-attr]
    assert [event.export_name_tuple[0] for event in invocation_events] == ["classify", "helper", "pick"]  # type: ignore[union-attr]


def test_generated_results_are_committed_as_their_validated_values() -> None:
    values: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        coerce = dispatch(CodeProposal(source="def coerce() -> int:\n    return '123'\n", export_name_tuple=("coerce",))).export_name_to_value[
            "coerce"
        ]
        values.append(coerce())  # type: ignore[operator]
        return EchoResult()

    handle = _run(body)
    assert values == [123]
    assert isinstance(values[0], int)
    outcome = [entry.event for entry in handle.ledger.select(DispatchOutcomeEvent)][-1]
    assert isinstance(outcome, DispatchOutcomeEvent)
    assert outcome.result == 123
    assert outcome.result_type_name == "builtins.int"


def test_definition_only_dispatch_rejects_a_definition_time_effect_before_it_runs_or_records_a_dispatch() -> None:
    touched: list[str] = []

    def touch() -> int:
        touched.append("touched")
        return 1

    grant = Grant.of(Capability(name="touch", function=touch, authority_boundary="test"))
    reasons: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        with pytest.raises(DispatchRejectedError) as captured:
            dispatch(
                CodeProposal(
                    source="def answer(value: int = touch()) -> int:\n    return value\n",
                    export_name_tuple=("answer",),
                    dependency_tuple=(DependencyDeclaration(name="touch", kind="capability", declared_type="() -> int"),),
                    effect_footprint=EffectFootprint(capability_name_set=frozenset({"touch"})),
                )
            )
        reasons.append(captured.value.reason)
        return EchoResult()

    handle = _run(body, grant=grant)
    assert reasons == ["materialization_effect"]
    assert touched == []
    assert handle.ledger.select(DispatchEvent) == ()


def test_dispatch_results_are_recorded_as_snapshots_that_later_mutation_cannot_alter() -> None:
    def body(run_input: EchoInput) -> EchoResult:
        exports = dispatch(
            CodeProposal(
                source="def build() -> list:\n    return [1, 2]\n\ndef pick():\n    return build\n",
                export_name_tuple=("build", "pick"),
            )
        ).export_name_to_value
        built = exports["build"]()  # type: ignore[operator]
        built.append(3)  # type: ignore[union-attr]
        exports["pick"]()  # type: ignore[operator]
        return EchoResult()

    handle = _run(body)
    outcomes = [entry.event for entry in handle.ledger.select(DispatchOutcomeEvent) if entry.event.result is not None]  # type: ignore[union-attr]
    assert outcomes[0].result == (1, 2)  # type: ignore[union-attr]
    assert isinstance(outcomes[1].result, GeneratedDefinition)  # type: ignore[union-attr]
    assert outcomes[1].result.export_name == "build"  # type: ignore[union-attr]


_SUSPENDABLE_SOURCE = (
    "def make_counter():\n"
    "    return _count()\n\n"
    "def make_delegating():\n"
    "    return _delegate()\n\n"
    "def make_async_counter():\n"
    "    return _count_async()\n\n"
    "def make_coroutine():\n"
    "    return _co()\n\n"
    "def advance(generator) -> int:\n"
    "    return next(generator)\n\n"
    "def snapshot_notes() -> list:\n"
    "    return list(notes)\n\n"
    "def _count():\n"
    "    try:\n"
    "        yield 1\n"
    "        yield 2\n"
    "    finally:\n"
    "        notes.append('finalized')\n\n"
    "def _delegate():\n"
    "    yield from [1, 2, 3]\n\n"
    "async def _count_async():\n"
    "    try:\n"
    "        yield 1\n"
    "    finally:\n"
    "        notes.append('async finalized')\n\n"
    "async def _co() -> int:\n"
    "    return 7\n"
)
_SUSPENDABLE_EXPORT_TUPLE = ("make_counter", "make_delegating", "make_async_counter", "make_coroutine", "advance", "snapshot_notes")


def _dispatch_suspendable(notes: list[str]) -> Mapping[str, object]:
    return dispatch(
        CodeProposal(
            source=_SUSPENDABLE_SOURCE,
            export_name_tuple=_SUSPENDABLE_EXPORT_TUPLE,
            dependency_tuple=(DependencyDeclaration(name="notes", kind="reference", declared_type=list),),
            effect_footprint=EffectFootprint(mutable_reference_name_set=frozenset({"notes"})),
        )
    ).export_name_to_value


def test_suspended_generated_generators_and_coroutines_refuse_every_resumption_outside_their_dispatch() -> None:
    notes: list[str] = []
    leaked: dict[str, object] = {}
    reasons: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        exports = _dispatch_suspendable(notes)
        counter = exports["make_counter"]()  # type: ignore[operator]
        delegating = exports["make_delegating"]()  # type: ignore[operator]
        assert exports["advance"](counter) == 1  # type: ignore[operator]
        assert exports["advance"](delegating) == 1  # type: ignore[operator]
        leaked.update(counter=counter, delegating=delegating, async_counter=exports["make_async_counter"](), coroutine=exports["make_coroutine"]())  # type: ignore[operator]
        coroutine = cast(Coroutine[Any, Any, int], leaked["coroutine"])
        for attempt in (
            lambda: next(delegating),
            lambda: counter.throw(ValueError("x")),
            lambda: counter.close(),
            lambda: asyncio.run(coroutine),
        ):  # type: ignore[attr-defined]
            with pytest.raises(DispatchRejectedError) as captured:
                attempt()
            reasons.append(captured.value.reason)
        return EchoResult()

    handle = _run(body, grant=Grant.of(notes=notes))
    assert reasons == ["unmediated_execution"] * 4
    assert notes == []
    with pytest.raises(DispatchRejectedError) as after_run:
        leaked["counter"].close()  # type: ignore[attr-defined]
    assert after_run.value.reason == "run_ended"
    with pytest.raises(DispatchRejectedError):
        next(leaked["delegating"])  # type: ignore[arg-type]
    with pytest.raises(DispatchRejectedError):
        asyncio.run(leaked["async_counter"].aclose())  # type: ignore[attr-defined]
    assert notes == []
    assert handle.ledger.entries[-1].event.kind == "run_end"


def test_generated_generators_and_coroutines_run_normally_inside_their_dispatch() -> None:
    source = (
        "def total() -> int:\n"
        "    return sum(_count())\n\n"
        "def delegated() -> list:\n"
        "    return list(_delegate())\n\n"
        "def finalized() -> list:\n"
        "    notes = []\n"
        "    generator = _noting(notes)\n"
        "    next(generator)\n"
        "    generator.close()\n"
        "    return notes\n\n"
        "async def doubled(value: int) -> int:\n"
        "    return await _co(value) * 2\n\n"
        "async def gathered() -> list:\n"
        "    return [item async for item in _count_async()]\n\n"
        "def _count():\n"
        "    yield 1\n"
        "    yield 2\n\n"
        "def _delegate():\n"
        "    yield from [3, 4]\n\n"
        "def _noting(notes):\n"
        "    try:\n"
        "        yield 1\n"
        "    finally:\n"
        "        notes.append('finalized')\n\n"
        "async def _co(value: int) -> int:\n"
        "    return value\n\n"
        "async def _count_async():\n"
        "    yield 5\n"
        "    yield 6\n"
    )
    observations: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        exports = dispatch(
            CodeProposal(source=source, export_name_tuple=("total", "delegated", "finalized", "doubled", "gathered"))
        ).export_name_to_value
        observations.append(exports["total"]())  # type: ignore[operator]
        observations.append(exports["delegated"]())  # type: ignore[operator]
        observations.append(exports["finalized"]())  # type: ignore[operator]
        observations.append(asyncio.run(exports["doubled"](21)))  # type: ignore[arg-type, operator]
        observations.append(asyncio.run(exports["gathered"]()))  # type: ignore[arg-type, operator]
        return EchoResult()

    _run(body)
    assert observations == [3, [3, 4], ["finalized"], 42, [5, 6]]


def test_context_copied_out_of_a_finished_dispatch_admits_no_further_generated_execution() -> None:
    import contextvars

    source = "def capture():\n    return contextvars.copy_context(), _helper\n\ndef _helper() -> int:\n    return 1\n"
    grant = Grant.of(contextvars=contextvars)
    observations: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        result = dispatch(
            CodeProposal(
                source=source,
                export_name_tuple=("capture",),
                dependency_tuple=(DependencyDeclaration(name="contextvars", kind="reference", declared_type=object),),
            )
        )
        captured_context, helper = result.export_name_to_value["capture"]()  # type: ignore[misc, operator]
        dispatch_count_before = current_run_ledger().dispatch_count()
        with pytest.raises(DispatchRejectedError) as rejected:
            captured_context.run(helper)
        observations.append(rejected.value.reason)
        with pytest.raises(DispatchRejectedError) as stale_dispatch:
            captured_context.run(dispatch, CodeProposal(source=_ADD_SOURCE, export_name_tuple=("add",)))
        observations.append(stale_dispatch.value.reason)
        observations.append(current_run_ledger().dispatch_count() == dispatch_count_before)
        return EchoResult()

    handle = _run(body, grant=grant)
    assert observations == ["unmediated_execution", "node_ended", True]
    anomaly_kinds = [entry.event.anomaly_kind for entry in handle.ledger.select(TraceAnomalyEvent)]  # type: ignore[union-attr]
    assert anomaly_kinds == ["unmediated_generated_execution"]
