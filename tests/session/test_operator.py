from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from nighthawk.harness.errors import CapabilityRejectedError
from nighthawk.harness.grants import Grant
from nighthawk.parts.conversation import ConversationHistoryQuery
from nighthawk.session import (
    CellInput,
    HistoryFilter,
    LocalBindingObservation,
    LocalQuery,
    ReadHistoryRequest,
    ReadLocalsRequest,
    ReadTextRequest,
    RenameLocalRequest,
)
from nighthawk.session._queries import _QuerySelection
from tests.support.session import session_manager


def test_complete_local_observation_has_controlled_time_and_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nighthawk.session._session.time.time", lambda: 100.0)
    session = session_manager().create_session()
    try:
        session.binding_name_to_value["samples"] = [1, 2, 3]
        row = session.locals.inspect("samples")
        assert row == LocalBindingObservation(
            "samples",
            "list",
            "[1, 2, 3]",
            "candidate",
            "Candidate for a future run; actual admission depends on that run's grant",
            100.0,
            session._observation_revision,
            False,
            True,
        )
        page = session.locals.read(LocalQuery(name="samples", revision=row.revision))
        assert page.binding_tuple == (row,) and page.observed_at == 100.0 and page.revision == row.revision
        assert page.next_starting_index is None and not page.incomplete
    finally:
        session.close()


def test_help_result_is_readable_without_changing_typed_values() -> None:
    from nighthawk.session import OperationDescriptor

    session = session_manager().create_session()
    try:
        outcome = session.submit(CellInput("help_items = session.help()\nhelp_items", mode="python")).wait()
        assert outcome.status == "success"
        value = session.binding_name_to_value["help_items"]
        assert type(value) is tuple and all(type(item) is OperationDescriptor for item in value)
        text = next(record.text for record in outcome.output_tuple if record.kind == "result")
        assert "session.exit() -> ExitResult\n  " in text
        assert "OperationDescriptor(" not in text
        assert not next(record for record in outcome.output_tuple if record.kind == "result").incomplete
        assert session.help("session.exit")[0] in value
        outcome = session.submit(CellInput("list(session.help('session.exit'))", mode="python")).wait()
        assert next(record.text for record in outcome.output_tuple if record.kind == "result").startswith("session.exit() -> ExitResult\n  ")
    finally:
        session.close()


def test_operator_queries_do_not_call_executor() -> None:
    session = session_manager().create_session()
    try:
        session.submit(CellInput("> samples = [1, 2, 3]\n_private = 7")).wait()
        page = session.operations.invoke(ReadLocalsRequest(LocalQuery(name="samples")))
        assert page.binding_tuple[0].preview == "[1, 2, 3]"  # type: ignore[union-attr]
        row = session.locals.inspect("samples")
        assert row.exposure == "candidate" and "future run" in row.exposure_description
        assert session.locals.inspect("_private").exposure == "private"
        assert session.locals.inspect("session").exposure == "controller"
        assert session.help("session.locals.rename")[0].signature == "(name: str, new_name: str) -> BindingMutationResult"
        assert session.operations.list("rename")[0].name == "session.locals.rename"
        assert session.execution.read().state == "success"
        assert session.background.list() == ()
        assert session.operations.invoke(ReadHistoryRequest(filters=HistoryFilter(kind="input"))).record_tuple[0].kind == "input"  # type: ignore[union-attr]
        text = session.operations.invoke(ReadTextRequest(0))
        assert text.text == "> samples = [1, 2, 3]\n_private = 7"  # type: ignore[union-attr]
        with pytest.raises(FrozenInstanceError):
            row.preview = "changed"  # type: ignore[misc]
    finally:
        session.close()


def test_operator_inspection_never_calls_arbitrary_repr_or_properties() -> None:
    class Uninspectable:
        def __repr__(self) -> str:
            raise AssertionError("repr was executed")

        @property
        def __signature__(self) -> object:
            raise AssertionError("property was executed")

        @property
        def __class__(self) -> type:
            raise AssertionError("class property was executed")

        @__class__.setter
        def __class__(self, value: type) -> None:
            raise AssertionError("class setter was executed")

    session = session_manager().create_session()
    try:
        value = Uninspectable()
        session.binding_name_to_value.update(opaque=value, nested=[value], samples=[1])
        opaque = session.locals.inspect("opaque")
        assert opaque.preview == "<preview unavailable>" and opaque.incomplete
        assert session.locals.inspect("nested").incomplete
        original = session.binding_name_to_value["samples"]
        assert session.operations.invoke(RenameLocalRequest("samples", "renamed")).status == "applied"  # type: ignore[union-attr]
        assert session.binding_name_to_value["renamed"] is original
        assert session.locals.rename("renamed", "opaque").status == "collision"
        assert session.locals.delete("missing").status == "missing"
        assert session.locals.delete("session").status == "unavailable"
        assert session.locals.delete("renamed").status == "applied"
        assert "renamed" not in session.binding_name_to_value
    finally:
        session.close()


def test_operator_aliases_are_not_agent_capabilities() -> None:
    session = session_manager().create_session()
    try:
        session.binding_name_to_value.update(
            controller=session.locals, rename=session.locals.rename, operator=session.operations, data=[session.locals]
        )
        grant, _ = session._selection()
        assert all(name not in grant for name in ("controller", "rename", "operator", "session"))
        assert "data" in grant  # Ordinary data retention is not recursively prohibited.
        session.submit(CellInput("> owner = session\nsession = 'shadowed'")).wait()
        assert session.locals.read().session_name_shadowed
        assert session.help("session.locals.read")
        assert session.binding_name_to_value["session"] == "shadowed"
    finally:
        session.close()


def test_operator_inspection_preserves_agent_history_horizon() -> None:
    session = session_manager().create_session()
    try:
        session.submit(CellInput("> original = 1")).wait()
        horizon = session.history._count
        selection = _QuerySelection(Grant.EMPTY, {}, session.history, horizon)
        session.submit(CellInput("> later = 2")).wait()
        observed = selection.query_history(ConversationHistoryQuery())
        assert observed.horizon == horizon
        assert not any("later" in record.text for record in observed.record_tuple)
        assert session.history.search("later").record_tuple
        with pytest.raises(ValueError, match="horizon"):
            selection.query_history(ConversationHistoryQuery(horizon=session.history._count))
    finally:
        session.close()


async def test_busy_local_mutation_is_not_queued() -> None:
    session = session_manager().create_session()
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        session.binding_name_to_value.update(samples=[1], entered=entered, release=release)
        execution = session.submit(CellInput("> entered.set()\nawait release.wait()"))
        task = asyncio.create_task(execution.wait_async())
        await entered.wait()
        before = session.locals.read()
        result = session.operations.invoke(RenameLocalRequest("samples", "renamed"))
        assert result.status == "busy"  # type: ignore[union-attr]
        assert session.locals.read().revision == before.revision
        descriptor = session.operations.list("rename")[0]
        assert not descriptor.available and "busy" in descriptor.unavailable_reason
        release.set()
        assert (await task).status == "success"
        assert "samples" in session.binding_name_to_value and "renamed" not in session.binding_name_to_value
        assert (await session.submit(CellInput("> result = session.locals.rename('samples', 'renamed')")).wait_async()).status == "success"
        assert session.binding_name_to_value["result"].status == "applied"
        assert session.binding_name_to_value["renamed"] == [1]
    finally:
        release.set()
        session.close()


async def test_inherited_background_task_is_not_an_operator_statement_boundary() -> None:
    session = session_manager().create_session()
    try:
        source = "> import asyncio\nsamples = [1]\nasync def mutate():\n    return session.locals.rename('samples', 'renamed')\nresult = await asyncio.create_task(mutate())"
        assert (await session.submit(CellInput(source)).wait_async()).status == "success"
        assert session.binding_name_to_value["result"].status == "busy"
    finally:
        session.close()


def test_operator_methods_reject_calls_inside_a_natural_run() -> None:
    session = session_manager().create_session()
    try:
        session.submit(CellInput("> @nh.function\ndef forbidden() -> None:\n    session.locals.read()")).wait()
        result = session.submit(CellInput("> forbidden()")).wait()
        assert result.status == "failure"
        assert any(CapabilityRejectedError.__name__ in record.text for record in result.output_tuple)
    finally:
        session.close()


def test_type_name_preview_does_not_execute_metaclass_properties_or_block_admission() -> None:
    class DescribedType(type):
        @property
        def __name__(class_object) -> str:  # type: ignore[override]  # noqa: N804
            raise AssertionError("type-name property executed")

    class Value(metaclass=DescribedType):
        pass

    session = session_manager().create_session()
    try:
        session.binding_name_to_value["value"] = Value()
        assert session.locals.inspect("value").type_description == "Value"
        execution = session.submit(CellInput("42", mode="python"))
        assert execution.wait().status == "success"
        assert session._active_execution is None
        assert execution.wait() is execution.outcome
    finally:
        session.close()


def test_failed_projection_never_occupies_admission_or_duplicates_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    session = session_manager().create_session()

    def failed_projection(observed_at: float, revision: int) -> None:
        raise ValueError("projection fixture failed")

    try:
        monkeypatch.setattr(session.locals, "_capture", failed_projection)
        execution = session.submit(CellInput("42", mode="python"))
        outcome = execution.wait()
        assert outcome.status == "success"
        assert execution.wait() is outcome
        assert session._active_execution is None
        assert "unavailable" in (session.locals.read().unavailable_reason or "")
        assert len([record for record in outcome.output_tuple if record.kind == "status"]) == 1
    finally:
        session.close()


async def test_outcome_filter_uses_confirmed_cell_completion_only() -> None:
    session = session_manager().create_session()
    try:
        first = session.submit(CellInput("42", mode="python"))
        assert not session.history.read(filters=HistoryFilter(outcome="success")).record_tuple
        await first.wait_async()
        second = session.submit(CellInput("raise ValueError('expected')", mode="python"))
        await second.wait_async()
        third = session.submit(CellInput("42", mode="python"))
        third.request_interrupt()
        await third.wait_async()
        for execution, filters in (
            (first, HistoryFilter(outcome="success")),
            (second, HistoryFilter(outcome="failure")),
            (third, HistoryFilter(outcome="cancelled")),
        ):
            records = session.history.read(filters=filters).record_tuple
            assert records and {record.cell_id for record in records} == {execution.cell_id}
    finally:
        session.close()


def test_namespace_admission_and_mutation_have_one_serialization_boundary() -> None:
    import threading

    session = session_manager().create_session()
    session.binding_name_to_value["samples"] = [1]
    entered = threading.Event()
    finished = threading.Event()
    results = []

    def mutate() -> None:
        entered.set()
        results.append(session.locals.rename("samples", "renamed"))
        finished.set()

    try:
        with session._namespace_lock:
            thread = threading.Thread(target=mutate)
            thread.start()
            assert entered.wait(5)
            execution = session.submit(CellInput("42", mode="python"))
            assert not finished.is_set()
        thread.join(5)
        assert not thread.is_alive()
        assert results[0].status == "busy"
        assert execution.wait().status == "success"
        assert "samples" in session.binding_name_to_value and "renamed" not in session.binding_name_to_value
    finally:
        session.close()


def test_source_candidate_preview_does_not_traverse_arbitrary_proposal_metadata() -> None:
    from nighthawk.parts.sources import SourceCandidate

    class UninspectableProposal:
        @property
        def source(self) -> str:
            raise AssertionError("arbitrary proposal property executed")

    session = session_manager().create_session()
    try:
        session.binding_name_to_value["candidate"] = SourceCandidate(UninspectableProposal(), "run", "cell")  # type: ignore[arg-type]
        observed = session.locals.inspect("candidate")
        assert observed.incomplete and observed.preview == "<preview unavailable>"
        assert session.locals.read().unavailable_reason is None
    finally:
        session.close()


@pytest.mark.parametrize("equal", [False, True])
def test_preview_type_identity_preserves_explicit_python_special_methods(equal: bool) -> None:
    calls: list[str] = []

    class Metadata(type):
        def __eq__(self, other: object) -> bool:  # noqa: N804
            calls.append("type equality")
            return equal

    class Value(metaclass=Metadata):
        def __repr__(self) -> str:
            calls.append("representation")
            return "value representation"

        def __str__(self) -> str:
            calls.append("string")
            return "value string"

        def __eq__(self, other: object) -> bool:
            calls.append("value equality")
            return True

    from nighthawk.parts.values import _require_basic_data
    from nighthawk.session._inspection import _ProjectionBudget

    value = Value()
    session = session_manager().create_session()
    try:
        session.binding_name_to_value["value"] = value
        budget = _ProjectionBudget(256)
        assert budget.value(value) is None and budget.incomplete
        with pytest.raises(ValueError, match="actual built-in"):
            _require_basic_data(value)
        assert session.locals.inspect("value").preview == "<preview unavailable>"
        assert session.submit(CellInput("42", mode="python")).wait().status == "success"
        assert calls == []
        outcome = session.submit(CellInput("repr(value); str(value); value == value; value", mode="python")).wait()
        assert outcome.status == "success"
        assert calls == ["representation", "string", "value equality", "representation"]
        assert outcome.output_tuple[-2].text == "value representation"
    finally:
        session.close()
