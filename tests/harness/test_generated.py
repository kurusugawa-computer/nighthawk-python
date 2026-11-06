from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

import pytest

from nighthawk.harness.confinement import unconfirmed_perimeter_confinement
from nighthawk.harness.context import current_grant, scope
from nighthawk.harness.errors import DispatchRejectedError, RunFailedError, RunRejectedError
from nighthawk.harness.generated import RetainedFunction, retain_function
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import DefinitionMaterializationEvent, DispatchEvent, PerimeterEffectAttemptEvent, PerimeterEffectCompletionEvent
from nighthawk.harness.perimeter import EffectFootprint, PerimeterPolicy
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.parts.procedures import materialize_procedure, selected_procedure
from nighthawk.parts.procedures.catalog import ProcedureCatalog
from tests.support.harness import make_runner

OFFSET = CodeProposal(
    source="def add(value: int) -> int:\n    return value + offset\n",
    export_name_tuple=("add",),
    dependency_tuple=(DependencyDeclaration(name="offset", kind="reference", declared_type=int),),
)


def run[R](
    body: Callable[[], R],
    *,
    grant: Grant = Grant.EMPTY,
    policy: PerimeterPolicy = PerimeterPolicy.NONE,
    handle: RunHandle | None = None,
    runner: Runner | None = None,
) -> R:
    return (runner or make_runner()).run(
        ScaffoldComposition(RunContract(type(None), object), lambda value: body(), run_entry_grant=grant, composition_perimeter_policy=policy),
        None,
        handle=handle,
    )  # type: ignore[return-value]


def test_retention_preserves_source_and_origin_but_resolves_each_runs_dependencies() -> None:
    first, second = RunHandle(), RunHandle()
    function = run(lambda: retain_function(OFFSET, export_name="add"), grant=Grant.of(offset=1), handle=first)
    assert isinstance(function, RetainedFunction)
    assert function.definition.originating_run_id == first.run_id
    assert len(first.ledger.select(DispatchEvent)) == 1
    original = first.ledger.select(DefinitionMaterializationEvent)[0].event
    assert isinstance(original, DefinitionMaterializationEvent)
    assert function.definition.definition_id == original.definition_id
    with pytest.raises(DispatchRejectedError):
        function(6)

    def body() -> None:
        assert function(6) == 16
        assert function(7) == 17
        with scope(current_grant().without(("offset",))), pytest.raises(DispatchRejectedError, match="dependency_missing"):
            function(6)
        with (
            scope(current_grant().without(("offset",)).with_references(offset="wrong")),
            pytest.raises(DispatchRejectedError, match="dependency_type_mismatch"),
        ):
            function(6)
        assert function(8) == 18

    run(body, grant=Grant.of(offset=10), handle=second)
    events = second.ledger.select(DefinitionMaterializationEvent)
    assert len(events) == 1
    assert isinstance(events[0].event, DefinitionMaterializationEvent)
    assert events[0].event.retained_definition_id == function.definition.definition_id
    assert events[0].event.definition_id != function.definition.definition_id
    assert events[0].event.source_provenance_class == "generated"
    assert events[0].event.approval_identity is None
    assert len(second.ledger.select(DispatchEvent)) == 4


def test_failed_rematerialization_does_not_cache_authority_or_revive_expired_references() -> None:
    function = run(lambda: retain_function(OFFSET, export_name="add"), grant=Grant.of(offset=1))
    escaped = run(function.materialize, grant=Grant.of(offset=2))
    for grant, runner in (
        (Grant.EMPTY, make_runner()),
        (Grant.of(offset=3), replace(make_runner(), perimeter_confinement=unconfirmed_perimeter_confinement())),
    ):
        handle = RunHandle()
        with pytest.raises(RunFailedError) as failure:
            run(lambda: function(6), grant=grant, runner=runner, handle=handle)
        assert isinstance(failure.value.__cause__, DispatchRejectedError)
        assert not handle.ledger.select(DispatchEvent)
    assert run(lambda: function(6), grant=Grant.of(offset=4)) == 10
    with pytest.raises(DispatchRejectedError):
        escaped(6)


def test_catalog_retains_definition_for_a_fresh_run_and_selection_uses_current_scope() -> None:
    catalog = ProcedureCatalog()
    selection = run(lambda: materialize_procedure(catalog, OFFSET, export_name="add", purpose="Add the current offset"), grant=Grant.of(offset=1))

    def body() -> int:
        with selected_procedure(catalog, selection) as function:
            assert callable(function)
            result = function(6)
            assert isinstance(result, int)
            return result

    assert run(body, grant=Grant.of(offset=100)) == 106


async def test_concurrent_runs_have_separate_materializations_and_async_ownership() -> None:
    both_started = asyncio.Event()
    arrivals: list[int] = []

    async def rendezvous(value: int) -> int:
        arrivals.append(value)
        if len(arrivals) == 2:
            both_started.set()
        await both_started.wait()
        return value

    capability = Capability(name="rendezvous", function=rendezvous, authority_boundary="test barrier")
    proposal = CodeProposal(
        source="async def count(value: int, values: list[int] = []) -> int:\n    values.append(value)\n    await rendezvous(value)\n    return len(values)\n",
        export_name_tuple=("count",),
        dependency_tuple=(DependencyDeclaration(name="rendezvous", kind="capability", declared_type=capability.declared_type),),
        effect_footprint=EffectFootprint(capability_name_set=frozenset({"rendezvous"})),
    )
    grant = Grant.of(capability)
    function = run(lambda: retain_function(proposal, export_name="count"), grant=grant)

    async def body(value: int) -> int:
        reference = function.materialize()
        result = reference(value)
        assert asyncio.iscoroutine(result)
        return await result

    runner = make_runner()
    composition = AsyncScaffoldComposition(RunContract(int, int), body, run_entry_grant=grant)
    handles = (RunHandle(), RunHandle())
    assert await asyncio.gather(*(runner.run(composition, index, handle=handle) for index, handle in enumerate(handles))) == [1, 1]
    assert {
        entry.event.retained_definition_id
        for handle in handles
        for entry in handle.ledger.select(DefinitionMaterializationEvent)
        if isinstance(entry.event, DefinitionMaterializationEvent)
    } == {function.definition.definition_id}


def test_retention_never_invokes_the_selected_body_and_rejects_noncallable_exports() -> None:
    proposal = CodeProposal(source="def fail() -> int:\n    raise ValueError('body invoked')\n", export_name_tuple=("fail",))
    function = run(lambda: retain_function(proposal, export_name="fail"))
    with pytest.raises(RunFailedError) as failure:
        run(lambda: function())
    assert isinstance(failure.value.__cause__, ValueError)
    with pytest.raises(RunFailedError) as failure:
        run(lambda: retain_function(CodeProposal(source="value: int = 1", export_name_tuple=("value",)), export_name="value"))
    assert isinstance(failure.value.__cause__, TypeError)


def test_rematerialization_links_only_the_selected_export_to_its_original_identity() -> None:
    proposal = CodeProposal(
        source="def first() -> int:\n    return second()\ndef second() -> int:\n    return 7\n",
        export_name_tuple=("first", "second"),
    )
    function = run(lambda: retain_function(proposal, export_name="first"))
    handle = RunHandle()
    assert run(lambda: function(), handle=handle) == 7
    export_name_to_origin = {
        entry.event.export_name: entry.event.retained_definition_id
        for entry in handle.ledger.select(DefinitionMaterializationEvent)
        if isinstance(entry.event, DefinitionMaterializationEvent)
    }
    assert export_name_to_origin == {"first": function.definition.definition_id, "second": None}


def test_retention_carries_neither_capability_implementation_nor_perimeter_permission() -> None:
    calls: list[tuple[str, str]] = []

    def old_emit(value: str) -> str:
        calls.append(("old", value))
        return value

    def new_emit(value: str) -> str:
        calls.append(("new", value))
        return value

    footprint = EffectFootprint(perimeter_effect_class_set=frozenset({"egress"}))
    old = Capability(name="emit", function=old_emit, authority_boundary="test effect double", effect_footprint=footprint)
    new = Capability(name="emit", function=new_emit, authority_boundary="fresh test effect double", effect_footprint=footprint)
    proposal = CodeProposal(
        source="def send(value: str) -> str:\n    return emit(value)\n",
        export_name_tuple=("send",),
        dependency_tuple=(DependencyDeclaration(name="emit", kind="capability", declared_type=old.declared_type),),
        effect_footprint=EffectFootprint(capability_name_set=frozenset({"emit"}), perimeter_effect_class_set=frozenset({"egress"})),
    )
    permitted = PerimeterPolicy.permitting("egress")
    function = run(lambda: retain_function(proposal, export_name="send"), grant=Grant.of(old), policy=permitted)
    assert calls == []
    with pytest.raises(RunFailedError) as failure:
        run(lambda: function("refused"), policy=permitted)
    assert isinstance(failure.value.__cause__, DispatchRejectedError)
    with pytest.raises(RunRejectedError, match="capability_outside_perimeter_policy"):
        run(lambda: function("refused"), grant=Grant.of(new))
    assert calls == []
    handle = RunHandle()
    assert run(lambda: function("accepted"), grant=Grant.of(new), policy=permitted, handle=handle) == "accepted"
    assert calls == [("new", "accepted")]
    assert len(handle.ledger.select(PerimeterEffectAttemptEvent)) == len(handle.ledger.select(PerimeterEffectCompletionEvent)) == 1
