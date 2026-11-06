from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any

import pytest

import nighthawk as nh
from nighthawk.core.step_request import StepCandidate
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.ledger import DefinitionMaterializationEvent
from nighthawk.session import CellInput, IncludedSourceApproval, RatifiedFunctionSource, SessionManager
from tests.support.session import session_manager


@nh.function
def _reviewed_unannotated():
    return 7


def test_explicit_result_registration_requires_independent_source_approval_and_does_not_transfer() -> None:
    registration = RatifiedFunctionSource(_reviewed_unannotated, inspect.getsource(_reviewed_unannotated), "reviewed-source-v1")
    for approve in (False, True):
        session = SessionManager(
            runner=session_manager()._runner,
            ratified_function_source_tuple=(registration,) if approve else (),
            function_to_result_type={_reviewed_unannotated: int},
        ).create_session()
        try:
            session.binding_name_to_value["reviewed"] = _reviewed_unannotated
            outcome = session.submit(CellInput("> alias = reviewed\nalias()")).wait()
            assert outcome.status == ("success" if approve else "failure")
            if approve:
                assert outcome.output_tuple[-2].text == "7"
                events = session._run_id_to_ledger[outcome.run_id_tuple[0]].select(DefinitionMaterializationEvent)
                assert any(
                    isinstance(entry.event, DefinitionMaterializationEvent) and entry.event.approval_identity == "reviewed-source-v1"
                    for entry in events
                )
                assert session.submit(CellInput("> @nh.function\ndef reviewed():\n    return 9")).wait().status == "success"
                failed = session.submit(CellInput("> reviewed()")).wait()
                assert failed.status == "failure" and failed.run_id_tuple == ()
                assert session.submit(CellInput("> alias()")).wait().status == "success"
        finally:
            session.close()


def test_forward_return_declaration_resolves_when_invoked_and_keeps_completed_async_type() -> None:
    session = session_manager().create_session()
    try:
        definition = session.submit(
            CellInput(
                "> from __future__ import annotations\ncalled = []\n@nh.function\nasync def answer() -> Later:\n    called.append(1)\n    return 7"
            )
        )
        assert definition.wait().status == "success"
        failed = session.submit(CellInput("> await answer()")).wait()
        assert failed.status == "failure" and failed.run_id_tuple == ()
        assert session.binding_name_to_value["called"] == []
        success = session.submit(CellInput("> Later = int\nawait answer()")).wait()
        assert success.status == "success" and len(success.run_id_tuple) == 1
        assert session.binding_name_to_value["called"] == [1]
    finally:
        session.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_approved_natural_result_preserves_application_identity_and_annotated_constraints(asynchronous: bool) -> None:
    session = session_manager().create_session()
    definition_prefix = "async def" if asynchronous else "def"
    call_prefix = "await " if asynchronous else ""
    source = (
        "from typing import Annotated\n"
        "from pydantic import Field\n"
        "class Document:\n    pass\n"
        "document = Document()\n"
        "@nh.function\n"
        f"{definition_prefix} read() -> Document:\n    return document\n"
        "@nh.function\n"
        f"{definition_prefix} positive() -> Annotated[int, Field(gt=0)]:\n    return -1\n"
    )
    try:
        outcome = session.submit(CellInput(source, mode="python")).wait()
        assert outcome.status == "success", outcome.output_tuple
        outcome = session.submit(CellInput(f"result = {call_prefix}read()", mode="python")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["result"] is session.binding_name_to_value["document"]
        outcome = session.submit(CellInput(f"invalid = {call_prefix}positive()", mode="python")).wait()
        assert outcome.status == "failure", outcome.output_tuple
        assert "invalid" not in session.binding_name_to_value
    finally:
        session.close()


def test_private_entry_call_is_allowed_but_discovery_requires_exposure_and_record_is_exact() -> None:
    session = session_manager().create_session()
    source = "@nh.function\ndef _private() -> int:\n    return 7\n"
    try:
        definition = session.submit(CellInput(source, mode="python"))
        assert definition.wait().status == "success"
        grant, functions = session._selection()
        assert "_private" not in grant and "_private" not in functions
        execution = session.submit(CellInput("> _private()"))
        outcome = execution.wait()
        assert outcome.status == "success"
        event = session._run_id_to_ledger[outcome.run_id_tuple[0]].select(DefinitionMaterializationEvent)[0].event
        assert isinstance(event, DefinitionMaterializationEvent)
        assert event.approval_route == "operator_submission"
        assert event.originating_cell_id == definition.cell_id
        assert event.defining_source_identity == hashlib.sha256(source.encode()).hexdigest()
        session.expose("_private")
        grant, functions = session._selection()
        assert "_private" in grant and "_private" in functions
        session.submit(CellInput("> del _private")).wait()
        assert "_private" not in session._selection()[0]
    finally:
        session.close()


async def test_callback_base_exception_records_cancellation_before_propagation() -> None:
    session = session_manager().create_session()

    def interrupt(output: Any) -> None:
        raise KeyboardInterrupt()

    try:
        execution = session.submit(CellInput("> earlier = 1\n42"))
        with pytest.raises(KeyboardInterrupt):
            await execution.wait_async(on_output=interrupt)
        assert execution.outcome is not None and execution.outcome.status == "cancelled"
        assert session.binding_name_to_value["earlier"] == 1
        assert (await session.submit(CellInput("> 3")).wait_async()).status == "success"
    finally:
        session.close()


def test_included_source_is_verified_at_definition_and_keeps_its_approved_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "instructions.md"
    approved_text = "Use the approved description."
    path.write_text(approved_text)
    manager = session_manager(StepCandidate(binding_name_to_value={"result": 7}))
    session = SessionManager(
        runner=manager._runner,
        included_source_approval_tuple=(IncludedSourceApproval(path, approved_text, "reviewed-instructions"),),
    ).create_session()
    source = '''@nh.function
def included() -> int:
    result: int = 0
    f"""execute
    {included_text}
    Set <:result> to 7.
    """
    return result
'''
    try:
        session.binding_name_to_value["included_text"] = approved_text
        assert session.submit(CellInput(source, mode="python")).wait().status == "success"
        path.write_text("Unapproved replacement.")
        outcome = session.submit(CellInput("> included()")).wait()
        assert outcome.status == "success", outcome.output_tuple
        executor = manager._runner.step_executor
        assert isinstance(executor, ScriptedExecutor)
        assert approved_text in executor.requests[0].processed_natural_program
        assert "Unapproved replacement" not in executor.requests[0].processed_natural_program
        original = session.binding_name_to_value["included"]
        failed = session.submit(CellInput(source, mode="python")).wait()
        assert failed.status == "failure" and failed.run_id_tuple == ()
        assert session.binding_name_to_value["included"] is original
    finally:
        session.close()
