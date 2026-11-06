from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nighthawk.adapters.files import FileSystemWriter
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.context import current_grant, scope
from nighthawk.harness.dispatch import GeneratedCallableReference, dispatch
from nighthawk.harness.errors import DispatchRejectedError
from nighthawk.harness.grants import Grant
from nighthawk.harness.ledger import DispatchEvent, NaturalExecutionOutcomeEvent, PerimeterEffectAttemptEvent, PerimeterEffectCompletionEvent
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract
from nighthawk.harness.testing import InMemoryPerimeterAuditStore
from nighthawk.parts.conversation import (
    ConversationInput,
    ConversationResult,
    DataExport,
    SourceExport,
    SourceInvocation,
    SourceRetention,
    UserFacingBlock,
)
from nighthawk.parts.files import FilePatchPolicy, file_patch_capability
from nighthawk.parts.sources import SourceCandidate
from nighthawk.session import CellInput, SessionManager
from nighthawk.session._conversation import perform_conversation_action
from tests.support.harness import FailingAuditStore, make_runner

PROPOSAL = CodeProposal(
    source="def add(value: int) -> int:\n    return value + offset\n",
    export_name_tuple=("add",),
    dependency_tuple=(DependencyDeclaration(name="offset", kind="reference", declared_type=int),),
)


def _manager(*responses: Any, **options: Any) -> SessionManager:
    # make_runner supplies confirming confinement and in-memory audit doubles, not deployment guarantees.
    runner = replace(make_runner(), step_executor=ScriptedExecutor(responses=list(responses)))
    return SessionManager(runner=runner, **options)


def test_standard_conversation_retains_exact_candidate_and_reuses_in_a_fresh_run() -> None:
    session = _manager(
        StepCandidate(binding_name_to_value={"result": ConversationResult((), {}, source_retention_tuple=(SourceRetention("candidate", PROPOSAL),))}),
        StepCandidate(binding_name_to_value={"result": SourceInvocation("candidate", "add", (6,))}),
        StepCandidate(binding_name_to_value={"result": ConversationResult((UserFacingBlock("answer", "7"),), {"total": 7})}),
    ).create_session()
    try:
        session.binding_name_to_value["offset"] = 1
        first = session.submit(CellInput("Keep the proposed add source as <:candidate>.")).wait()
        assert first.status == "success", first.output_tuple
        candidate = session.binding_name_to_value["candidate"]
        assert isinstance(candidate, SourceCandidate)
        assert candidate.proposal == PROPOSAL
        assert candidate.originating_run_id == first.run_id_tuple[0]
        assert candidate.originating_cell_id == first.cell_id
        assert not callable(candidate)
        second = session.submit(CellInput("Reuse candidate on 6 and write <:total>.")).wait()
        assert second.status == "success", second.output_tuple
        assert first.run_id_tuple != second.run_id_tuple
        assert session.binding_name_to_value["total"] == 7
        ledger = session._run_id_to_ledger[second.run_id_tuple[0]]
        assert len(ledger.select(DispatchEvent)) == 2
        outcomes = ledger.select(NaturalExecutionOutcomeEvent)
        assert len(outcomes) == 2
        assert isinstance(outcomes[0].event, NaturalExecutionOutcomeEvent)
        assert outcomes[0].event.committed_binding_name_to_value["result"] is not None
    finally:
        session.close()


async def test_expired_callable_stays_expired_after_reuse_and_explicit_operator_approval() -> None:
    escaped: list[Any] = []

    async def body(value: ConversationInput) -> ConversationResult:
        if value.task == "retain <:candidate>":
            materialized = dispatch(PROPOSAL)
            function = materialized.export_name_to_value["add"]
            assert isinstance(function, GeneratedCallableReference) and function(6) == 7
            escaped.append(function)
            return ConversationResult((), {}, source_retention_tuple=(SourceRetention("candidate", PROPOSAL),))
        assert await perform_conversation_action(SourceInvocation("candidate", "add", (6,))) == 8
        return ConversationResult(())

    session = _manager(
        conversation_composition_builder=lambda grant, policy: AsyncScaffoldComposition(
            RunContract(ConversationInput, ConversationResult),
            body,
            run_entry_grant=grant,
            composition_perimeter_policy=policy,
        )
    ).create_session()
    try:
        session.binding_name_to_value["offset"] = 1
        first = await session.submit(CellInput("retain <:candidate>")).wait_async()
        assert first.status == "success", first.output_tuple
        with pytest.raises(DispatchRejectedError):
            escaped[0](6)
        session.binding_name_to_value["offset"] = 2
        second = await session.submit(CellInput("reuse")).wait_async()
        assert second.status == "success", second.output_tuple
        assert first.run_id_tuple != second.run_id_tuple
        assert (await session.submit(CellInput(PROPOSAL.source, mode="python")).wait_async()).status == "success"
        assert session.binding_name_to_value["add"](6) == 8
        with pytest.raises(DispatchRejectedError):
            escaped[0](6)
        assert "candidate" in session.binding_name_to_value
    finally:
        session.close()


@pytest.mark.parametrize("offset,removed", [(None, True), ("wrong", False)])
async def test_reuse_resolves_new_grant_dependencies_before_body(offset: object, removed: bool) -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        with pytest.raises(DispatchRejectedError) as caught:
            await perform_conversation_action(SourceInvocation("candidate", "add", (6,)))
        assert caught.value.reason in ("dependency_missing", "dependency_type_mismatch")
        return ConversationResult(())

    session = _manager(
        conversation_composition_builder=lambda grant, policy: AsyncScaffoldComposition(
            RunContract(ConversationInput, ConversationResult),
            body,
            run_entry_grant=grant.without(("offset",)) if removed else grant,
            composition_perimeter_policy=policy,
        )
    ).create_session()
    try:
        session.binding_name_to_value.update(candidate=SourceCandidate(PROPOSAL, "old-run", "old-cell"), offset=offset)
        outcome = await session.submit(CellInput("reuse")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        assert not session._run_id_to_ledger[outcome.run_id_tuple[0]].select(DispatchEvent)
    finally:
        session.close()


async def test_reuse_cannot_bypass_nested_attenuation_or_use_expired_reference() -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        with scope(current_grant().without(("offset",))), pytest.raises(DispatchRejectedError):
            await perform_conversation_action(SourceInvocation("candidate", "add", (6,)))
        assert await perform_conversation_action(SourceInvocation("candidate", "add", (6,))) == 7
        return ConversationResult(())

    session = _manager(
        conversation_composition_builder=lambda grant, policy: AsyncScaffoldComposition(
            RunContract(ConversationInput, ConversationResult),
            body,
            run_entry_grant=grant,
            composition_perimeter_policy=policy,
        )
    ).create_session()
    try:
        session.binding_name_to_value.update(candidate=SourceCandidate(PROPOSAL, "old", "old"), offset=1)
        outcome = await session.submit(CellInput("reuse")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
    finally:
        session.close()


def test_source_retention_is_atomic_and_model_cannot_supply_origin_fields() -> None:
    session = _manager(
        StepCandidate(
            binding_name_to_value={
                "result": ConversationResult(
                    (),
                    {"first": 1},
                    source_retention_tuple=(
                        SourceRetention("candidate", PROPOSAL),
                        SourceRetention("invalid-target", PROPOSAL),
                    ),
                )
            }
        )
    ).create_session()
    try:
        outcome = session.submit(CellInput("retain source in <:first> and <:candidate>")).wait()
        assert outcome.status == "failure"
        assert "first" not in session.binding_name_to_value
        assert "candidate" not in session.binding_name_to_value
        assert not any(output.kind == "publication" for output in outcome.output_tuple)
    finally:
        session.close()
    malformed = {"block_tuple": (), "source_retention_tuple": ({"binding_name": "candidate", "proposal": PROPOSAL, "originating_run_id": "forged"},)}
    session = _manager(StepCandidate(binding_name_to_value={"result": malformed})).create_session()
    try:
        outcome = session.submit(CellInput("retain source in <:first> and <:candidate>")).wait()
        assert outcome.status == "failure"
        assert "candidate" not in session.binding_name_to_value
    finally:
        session.close()


def test_configured_confinement_refuses_reuse_once_and_reports_failure() -> None:
    from nighthawk.harness.confinement import unconfirmed_perimeter_confinement
    from tests.support.session import session_manager

    session = session_manager(StepCandidate(binding_name_to_value={"result": SourceInvocation("candidate", "add", (6,))})).create_session()
    session._runner = replace(session._runner, perimeter_confinement=unconfirmed_perimeter_confinement())
    try:
        session.binding_name_to_value.update(candidate=SourceCandidate(PROPOSAL, "old", "old"), offset=1)
        outcome = session.submit(CellInput("reuse candidate")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert any(output.kind == "failure" and "confinement" in output.text for output in outcome.output_tuple)
        ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
        assert len(ledger.select(NaturalExecutionOutcomeEvent)) == 1
        assert not ledger.select(DispatchEvent)
    finally:
        session.close()


def _export_manager(directory: Path, action: SourceExport | DataExport, *, audit: Any = None, allow_write: bool = True) -> SessionManager:
    grant = Grant((file_patch_capability(policy=FilePatchPolicy(path_write_allow_list=(directory,)), file_writer=FileSystemWriter()),))
    if not allow_write:
        grant = Grant.EMPTY
    runner = replace(
        make_runner(audit_store=audit),
        step_executor=ScriptedExecutor(
            responses=[
                StepCandidate(binding_name_to_value={"result": action}),
                StepCandidate(binding_name_to_value={"result": ConversationResult((UserFacingBlock("answer", "exported"),))}),
            ]
        ),
    )
    return SessionManager(runner=runner, run_entry_grant=grant, composition_perimeter_policy=runner.perimeter_policy)


def test_selected_source_and_data_export_use_real_files_and_audit_doubles(tmp_path: Path) -> None:
    audit = InMemoryPerimeterAuditStore()
    for action in (SourceExport("candidate", str(tmp_path / "selected.py")), DataExport("selected", str(tmp_path / "selected.json"))):
        session = _export_manager(tmp_path, action, audit=audit).create_session()
        try:
            session.binding_name_to_value.update(
                candidate=SourceCandidate(PROPOSAL, "origin-run", "origin-cell"), selected={"total": 7}, private="not exported"
            )
            outcome = session.submit(CellInput("Export the selected value.")).wait()
            assert outcome.status == "success", outcome.output_tuple
            assert not any(output.kind == "failure" for output in outcome.output_tuple), outcome.output_tuple
            ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
            assert len(ledger.select(PerimeterEffectAttemptEvent)) == len(ledger.select(PerimeterEffectCompletionEvent)) == 1
            assert not ledger.select(DispatchEvent)
        finally:
            session.close()
    assert (tmp_path / "selected.py").read_text() == PROPOSAL.source
    metadata = json.loads((tmp_path / "selected.py.json").read_text())
    assert metadata["originating_run_id"] == "origin-run"
    assert metadata["dependency_tuple"] == [{"name": "offset", "kind": "reference", "declared_type": "<class 'int'>"}]
    assert json.loads((tmp_path / "selected.json").read_text()) == {"total": 7}
    assert len(audit.records) == 4
    assert sorted(path.name for path in tmp_path.iterdir()) == ["selected.json", "selected.py", "selected.py.json"]


@pytest.mark.parametrize("failure", ["authority", "audit", "unsupported", "nonfinite"])
def test_export_refuses_before_write_when_unavailable_or_not_json_compatible(tmp_path: Path, failure: str) -> None:
    session = _export_manager(
        tmp_path,
        DataExport("selected", str(tmp_path / "value.json")),
        audit=FailingAuditStore() if failure == "audit" else None,
        allow_write=failure != "authority",
    ).create_session()
    try:
        session.binding_name_to_value["selected"] = {"bad": object()} if failure == "unsupported" else float("nan") if failure == "nonfinite" else 7
        outcome = session.submit(CellInput("export selected")).wait()
        assert any(output.kind == "failure" for output in outcome.output_tuple), outcome.output_tuple
        assert not tuple(tmp_path.iterdir())
    finally:
        session.close()


async def test_standard_async_source_action_feeds_actual_result_to_next_natural_block() -> None:
    from nighthawk.core.assignment import assign_expression_async

    proposal = CodeProposal(
        source="async def add(value: int) -> int:\n    return value + offset\n",
        export_name_tuple=("add",),
        dependency_tuple=PROPOSAL.dependency_tuple,
    )

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            if step_context.step_locals["action_result"] is None:
                await assign_expression_async(
                    step_context, "result", 'SourceInvocation(candidate_name="candidate", export_name="add", argument_tuple=(6,))'
                )
            else:
                assert step_context.step_locals["action_result"] == 7
                await assign_expression_async(
                    step_context, "result", 'ConversationResult(block_tuple=(), binding_name_to_value={"actual": action_result})'
                )
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": step_context.environment.step_name_to_value["result"]})

    session = SessionManager(runner=replace(make_runner(), step_executor=Executor())).create_session()
    try:
        session.binding_name_to_value.update(candidate=SourceCandidate(proposal, "old", "old"), offset=1)
        outcome = await session.submit(CellInput("reuse and write <:actual>")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["actual"] == 7
        assert len(session._run_id_to_ledger[outcome.run_id_tuple[0]].select(DispatchEvent)) == 2
    finally:
        session.close()


async def test_export_obeys_current_attenuation_and_never_calls_writer_directly(tmp_path: Path) -> None:

    async def body(value: ConversationInput) -> ConversationResult:
        with scope(current_grant().without(("apply_file_patch",))), pytest.raises(LookupError):
            await perform_conversation_action(DataExport("selected", str(tmp_path / "data.json")))
        assert not (tmp_path / "data.json").exists()
        await perform_conversation_action(DataExport("selected", str(tmp_path / "data.json")))
        return ConversationResult(())

    grant = Grant((file_patch_capability(policy=FilePatchPolicy(path_write_allow_list=(tmp_path,)), file_writer=FileSystemWriter()),))
    runner = make_runner()
    session = SessionManager(
        runner=runner,
        run_entry_grant=grant,
        composition_perimeter_policy=runner.perimeter_policy,
        conversation_composition_builder=lambda selected, policy: AsyncScaffoldComposition(
            RunContract(ConversationInput, ConversationResult),
            body,
            run_entry_grant=selected,
            composition_perimeter_policy=policy,
        ),
    ).create_session()
    try:
        session.binding_name_to_value["selected"] = 7
        outcome = await session.submit(CellInput("export selected")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        assert json.loads((tmp_path / "data.json").read_text()) == 7
    finally:
        session.close()
