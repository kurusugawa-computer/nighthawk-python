from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest

from nighthawk.harness.context import current_capabilities, current_grant, current_run_ledger, scope
from nighthawk.harness.errors import CapabilityRejectedError
from nighthawk.harness.evidence import EvidenceReference
from nighthawk.harness.grants import Grant
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract
from nighthawk.parts.conversation import (
    ContextQuery,
    ConversationHistoryQuery,
    ConversationInput,
    ConversationResult,
    FunctionSelection,
    UserFacingBlock,
)
from nighthawk.session import CellInput, SessionLookupError, SessionManager
from tests.support.session import configured, session_manager


def test_removed_functions_and_nested_attenuation_apply_to_queries_and_retained_resolver() -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        capabilities = current_capabilities()
        query = cast(Callable[..., Any], capabilities["query_session_functions"])
        resolve = cast(Callable[..., Any], capabilities["resolve_session_function"])
        assert [item.name for item in value.function_page.item_tuple] == ["g"]
        assert value.function_page.horizon == 1
        page = query(ContextQuery())
        assert [item.name for item in page.item_tuple] == ["g"]
        assert page.horizon == 1
        assert query(ContextQuery(starting_index=1)).item_tuple == ()
        with pytest.raises(SessionLookupError):
            resolve(FunctionSelection("f"))
        assert resolve(FunctionSelection("g"))(6) == 7
        with scope(current_grant().without(("g",))):
            page = query(ContextQuery())
            assert page.item_tuple == () and page.horizon == 1
            with pytest.raises(SessionLookupError):
                resolve(FunctionSelection("g"))
        assert resolve(FunctionSelection("g"))(7) == 8
        with scope(current_grant().without(("resolve_session_function",))), pytest.raises(CapabilityRejectedError):
            resolve(FunctionSelection("g"))
        return ConversationResult((UserFacingBlock("answer", "done"),))

    session = configured(body, remove=("f",)).create_session()
    try:
        session.submit(CellInput("> def f(value: int) -> int:\n    return value + 2\ndef g(value: int) -> int:\n    return value + 1")).wait()
        outcome = session.submit(CellInput("Use the available functions.")).wait()
        assert outcome.status == "success", outcome.output_tuple
    finally:
        session.close()


@pytest.mark.parametrize("natural", [False, True])
def test_async_function_discovery_preserves_the_await_contract(natural: bool) -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        assert [(item.name, item.text) for item in value.function_page.item_tuple] == [("describe", "async (value: int) -> str")]
        query = cast(Callable[..., Any], current_capabilities()["query_session_functions"])
        assert query(ContextQuery()).item_tuple == value.function_page.item_tuple
        resolve = cast(Callable[..., Any], current_capabilities()["resolve_session_function"])
        assert await resolve(FunctionSelection("describe"))(6) == "6"
        return ConversationResult(())

    session = configured(body).create_session()
    try:
        decorator = "@nh.function\n" if natural else ""
        source = f"{decorator}async def describe(value: int) -> str:\n    return str(value)"
        assert session.submit(CellInput(source, mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("Call describe with 6.")).wait()
        assert outcome.status == "success", "\n".join(output.text for output in outcome.output_tuple)
    finally:
        session.close()


def test_history_adapter_uses_captured_horizon_and_searches_beyond_record_two_hundred() -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        history = cast(Callable[..., Any], current_capabilities()["query_session_history"])
        page = history(ConversationHistoryQuery(query="rare needle"))
        assert len(page.record_tuple) == 1
        assert page.record_tuple[0].record_index > 200
        assert page.horizon == value.recent_history.horizon
        assert all("rare needle" not in record.text for record in value.recent_history.record_tuple)
        with pytest.raises(ValueError):
            history(ConversationHistoryQuery(horizon=page.horizon + 1))
        with scope(current_grant().without(("query_session_history",))), pytest.raises(CapabilityRejectedError):
            history(ConversationHistoryQuery())
        return ConversationResult((UserFacingBlock("answer", page.record_tuple[0].text),))

    session = configured(body).create_session()
    try:
        for i in range(105):
            session.submit(CellInput(f"> # older record {i}")).wait()
        session.submit(CellInput("> # rare needle")).wait()
        for i in range(25):
            session.submit(CellInput(f"> # recent record {i}")).wait()
        outcome = session.submit(CellInput("Search for the older distinctive note.")).wait()
        assert outcome.status == "success", outcome.output_tuple
    finally:
        session.close()


def test_removed_history_has_no_initial_page_or_later_wrapper() -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        assert value.recent_history.record_tuple == ()
        assert "query_session_history" not in current_capabilities()
        return ConversationResult(())

    session = configured(body, remove=("query_session_history",)).create_session()
    try:
        assert session.submit(CellInput("hello")).wait().status == "success"
    finally:
        session.close()


def test_projection_does_not_repr_custom_values_or_scan_whole_large_collections() -> None:
    class Hostile:
        def __repr__(self) -> str:
            raise AssertionError("custom repr must not run")

    async def body(value: ConversationInput) -> ConversationResult:
        items = {item.name: item for item in value.namespace_page.item_tuple}
        assert items["custom"].incomplete
        assert items["cyclic"].incomplete
        assert items["large"].incomplete
        assert len(cast(list[object], items["large"].value)) < 200
        assert value.namespace_page.incomplete
        return ConversationResult(())

    session = configured(body).create_session()
    try:
        cyclic: list[object] = []
        cyclic.append(cyclic)
        session.binding_name_to_value.update(custom=Hostile(), cyclic=cyclic, large=list(range(100000)))
        outcome = session.submit(CellInput("Inspect the available values.")).wait()
        assert outcome.status == "success", outcome.output_tuple
    finally:
        session.close()


def test_projection_counts_entries_omitted_at_the_depth_limit() -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        item = value.namespace_page.item_tuple[0]
        assert item.name == "deep" and item.incomplete
        projected = item.value
        for _ in range(4):
            assert isinstance(projected, list) and len(projected) == 1
            projected = projected[0]
        assert isinstance(projected, list) and len(projected) < 200
        return ConversationResult(())

    session = configured(body).create_session()
    try:
        deep: object = list(range(100000))
        for _ in range(4):
            deep = [deep]
        session.binding_name_to_value["deep"] = deep
        outcome = session.submit(CellInput("Inspect the nested value.")).wait()
        assert outcome.status == "success", "\n".join(output.text for output in outcome.output_tuple)
    finally:
        session.close()


def test_undeclared_later_write_prevents_the_complete_write_set() -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        return ConversationResult((), {"first": 6, "not a name": 7})

    session = configured(body).create_session()
    try:
        outcome = session.submit(CellInput("Keep both results in <:first> and <:second>.")).wait()
        assert outcome.status == "failure"
        assert "first" not in session.binding_name_to_value
        assert not any(output.kind == "publication" for output in outcome.output_tuple)
    finally:
        session.close()


def test_session_uses_core_binding_grammar_for_unicode_names() -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        return ConversationResult((), {"合計": 6})

    session = configured(body).create_session()
    try:
        assert session.submit(CellInput("Retain the value under <:合計>.")).wait().status == "failure"
        assert "合計" not in session.binding_name_to_value
        assert session.submit(CellInput("> 合計 = 6\n合計")).wait().output_tuple[0].text == "6"
    finally:
        session.close()


@pytest.mark.parametrize("valid", [False, True])
def test_citations_retain_real_event_identity_and_reject_nonexistent_value_paths(valid: bool) -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        ledger = current_run_ledger()
        reference = EvidenceReference(
            run_id=ledger.run_id,
            event_id=ledger.entries[0].event_id,
            value_path_tuple=("input_type_name",) if valid else ("invented_field",),
        )
        return ConversationResult((UserFacingBlock("answer", "This run has recorded entry evidence.", (reference,)),))

    session = configured(body).create_session()
    try:
        outcome = session.submit(CellInput("Describe entry evidence.")).wait()
        assert outcome.status == ("success" if valid else "failure")
        if valid:
            answer = next(output for output in outcome.output_tuple if output.kind == "answer")
            assert answer.evidence_reference_tuple[0].run_id == outcome.run_id_tuple[0]
    finally:
        session.close()


def test_malformed_typed_result_publishes_nothing_and_custom_reference_uses_final_grant() -> None:
    async def invalid(value: ConversationInput) -> ConversationResult:
        return ConversationResult((), {"first": 6, "bad": cast(Any, object())})

    session = configured(invalid).create_session()
    try:
        session.submit(CellInput("> bad: int = 0")).wait()
        outcome = session.submit(CellInput("Keep results in <:first> and <:bad>.")).wait()
        assert outcome.status == "failure"
        assert "first" not in session.binding_name_to_value
    finally:
        session.close()

    original = object()

    async def retain(value: ConversationInput) -> ConversationResult:
        return ConversationResult((), {"alias": current_grant().references["original"]})

    for removed in ((), ("original",)):
        session = configured(retain, remove=removed).create_session()
        try:
            session.binding_name_to_value["original"] = original
            outcome = session.submit(CellInput("Assign <original> to <:alias>.")).wait()
            assert outcome.status == ("failure" if removed else "success")
            if not removed:
                assert session.binding_name_to_value["alias"] is original
        finally:
            session.close()


@pytest.mark.parametrize("widen_policy", [False, True])
def test_builder_cannot_widen_reference_or_perimeter_ceiling(widen_policy: bool) -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        raise AssertionError("rejected builder body must never execute")

    def builder(grant: Grant, policy: PerimeterPolicy) -> AsyncScaffoldComposition[ConversationInput, ConversationResult]:
        composition = AsyncScaffoldComposition(RunContract(ConversationInput, ConversationResult), body, run_entry_grant=grant)
        if widen_policy:
            return replace(composition, composition_perimeter_policy=PerimeterPolicy.permitting_all())
        return replace(composition, run_entry_grant=grant.with_references(extra=object()))

    session = SessionManager(runner=session_manager()._runner, conversation_composition_builder=builder).create_session()
    try:
        outcome = session.submit(CellInput("run")).wait()
        assert outcome.status == "failure" and outcome.run_id_tuple == ()
    finally:
        session.close()
