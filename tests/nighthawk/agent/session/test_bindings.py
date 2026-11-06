"""Session cells share Natural binding grammar, types, and atomic assignment."""

from __future__ import annotations

from typing import Any, cast

import pytest

from nighthawk.agent import NaturalExecutionOutcomeEvent, PerimeterPolicy, RunEndEvent
from nighthawk.agent.runtime import Runner, bounded_resource_allocation
from nighthawk.agent.session import CellInput, ConversationResult, SessionManager
from nighthawk.runtime.assignment import assign_expression_async
from nighthawk.runtime.expression import evaluate_expression_async
from nighthawk.runtime.step_outcome import Pass
from nighthawk.runtime.step_request import StepCandidate, StepRequest

from .test_session import session_manager


def messages(outcome: Any) -> str:
    return "\n".join(output.text for output in outcome.output_tuple)


@pytest.mark.parametrize("declaration", ["value: int = 1", "value = 1", "value: int", "from __future__ import annotations\nvalue: int = 1"])
def test_write_binding_uses_python_annotation_or_core_initial_value_inference(declaration: str) -> None:
    session = session_manager(StepCandidate(binding_name_to_value={"result": ConversationResult(()), "value": "10"})).create_session()
    try:
        assert session.submit(CellInput(declaration, mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("Assign 10 to <:value>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["value"] == 10
        assert type(session.binding_name_to_value["value"]) is int
        ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
        event = ledger.select(NaturalExecutionOutcomeEvent)[0].event
        assert isinstance(event, NaturalExecutionOutcomeEvent)
        assert event.committed_binding_name_to_value["value"] == 10
        end = ledger.select(RunEndEvent)[0].event
        assert isinstance(end, RunEndEvent)
        assert "value" in str(end)
    finally:
        session.close()


@pytest.mark.parametrize("declaration", ["value: object = 1", "from typing import Any\nvalue: Any = 1"])
def test_explicit_unconstrained_annotation_does_not_infer_the_initial_integer_type(declaration: str) -> None:
    session = session_manager(StepCandidate(binding_name_to_value={"result": ConversationResult(()), "value": "text"})).create_session()
    try:
        assert session.submit(CellInput(declaration, mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("Assign text to <:value>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["value"] == "text"
    finally:
        session.close()


@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("through_result", [False, True])
def test_annotated_binding_constraints_survive_both_conversation_write_paths(valid: bool, through_result: bool) -> None:
    binding_name_to_value = {"first": 10, "positive": "7" if valid else -1}
    response = (
        {"result": ConversationResult((), binding_name_to_value)} if through_result else {"result": ConversationResult(()), **binding_name_to_value}
    )
    session = session_manager(StepCandidate(binding_name_to_value=response)).create_session()
    try:
        source = "from typing import Annotated\nfrom pydantic import Field\nfirst: int = 1\npositive: Annotated[int, Field(gt=0)] = 2"
        assert session.submit(CellInput(source, mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("Write <:first> and <:positive>.")).wait()
        assert outcome.status == ("success" if valid else "failure"), messages(outcome)
        assert session.binding_name_to_value["first"] == (10 if valid else 1)
        assert session.binding_name_to_value["positive"] == (7 if valid else 2)
        if not valid:
            assert not any(output.kind == "publication" for output in outcome.output_tuple)
    finally:
        session.close()


@pytest.mark.parametrize("invalid", [{"second": "invalid"}, {"undeclared": 10}])
def test_invalid_write_set_commits_nothing(invalid: dict[str, object]) -> None:
    class Executor:
        async def run_step_async(self, **options: Any) -> Any:
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": ConversationResult(()), "first": 10, **invalid})

    manager = session_manager()
    from dataclasses import replace

    manager._runner = replace(manager._runner, step_executor=Executor())
    session = manager.create_session()
    try:
        session.submit(CellInput("first: int = 1\nsecond: int = 2", mode="python")).wait()
        outcome = session.submit(CellInput("Write <:first> and <:second>.")).wait()
        assert outcome.status == "failure", messages(outcome)
        assert session.binding_name_to_value["first"] == 1
        assert session.binding_name_to_value["second"] == 2
        assert "undeclared" not in session.binding_name_to_value
        assert not any(output.kind == "publication" for output in outcome.output_tuple)
    finally:
        session.close()


def test_plain_prose_and_historical_markers_do_not_declare_writes() -> None:
    session = session_manager(StepCandidate(binding_name_to_value={"result": ConversationResult((), {"saved": 10})})).create_session()
    try:
        session.submit(CellInput("# Previously requested <:saved>", mode="python")).wait()
        outcome = session.submit(CellInput("Save 10 as saved.")).wait()
        assert outcome.status == "failure"
        assert "saved" not in session.binding_name_to_value
        assert "Undeclared output binding" in messages(outcome)
    finally:
        session.close()


def test_new_unannotated_binding_and_unassigned_binding_follow_core_rules() -> None:
    session = session_manager(StepCandidate(binding_name_to_value={"result": ConversationResult(()), "created": [1, 2]})).create_session()
    try:
        session.submit(CellInput("unchanged = 7", mode="python")).wait()
        outcome = session.submit(CellInput("Declare <:unchanged> and write <:created>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["created"] == [1, 2]
        assert session.binding_name_to_value["unchanged"] == 7
    finally:
        session.close()


def test_public_lambda_is_discovered_and_explicit_private_reads_are_run_local() -> None:
    calls: list[tuple[str, ...]] = []

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            context = cast(Any, step_context.step_locals["conversation_input"])
            calls.append(tuple(item.name for item in context.function_page.item_tuple))
            assert "f" in calls[-1] and "alias" in calls[-1]
            assert "_private" in calls[-1]
            assert "_private" in step_context.read_binding_names
            assert await evaluate_expression_async(step_context, 'resolve_session_function(FunctionSelection("f"))(10)') == 20
            assert await evaluate_expression_async(step_context, 'resolve_session_function(FunctionSelection("alias"))(10)') == 20
            await assign_expression_async(step_context, "answer", "_private(10)")
            return StepCandidate(
                outcome=Pass(),
                binding_name_to_value={
                    "result": ConversationResult(()),
                    "answer": step_context.environment.step_name_to_value["answer"],
                },
            )

    session = SessionManager(
        runner=Runner(Executor(), PerimeterPolicy.NONE, lambda identity: bounded_resource_allocation(), accepts_aggregate_executor_accounting=True)
    ).create_session()
    try:
        source = "f = lambda a: a + 10\nalias = f\n_private = lambda a: a + 20"
        assert session.submit(CellInput(source, mode="python")).wait().status == "success"
        assert "_private" not in session._selection()[0]
        outcome = session.submit(CellInput("Call <_private> with 10 and write <:answer>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["answer"] == 30
        assert "_private" not in session._selection()[0]
        assert len(calls) == 1
    finally:
        session.close()


def test_missing_or_ineligible_explicit_read_fails_before_model_access() -> None:
    session = session_manager().create_session()
    try:
        for task in ("Read <missing>.", "Read <session>."):
            outcome = session.submit(CellInput(task)).wait()
            assert outcome.status == "failure" and outcome.run_id_tuple == ()
            assert "read binding is unavailable" in messages(outcome)
    finally:
        session.close()


def test_user_names_do_not_collide_with_conversation_control_bindings() -> None:
    session = session_manager(
        StepCandidate(binding_name_to_value={"_result": ConversationResult(()), "result": 10, "conversation_input": "changed", "action_result": 20})
    ).create_session()
    try:
        session.submit(CellInput("result: int = 1\nconversation_input: str = 'original'\naction_result: int = 2", mode="python")).wait()
        outcome = session.submit(CellInput("Write <:result>, <:conversation_input>, and <:action_result>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["result"] == 10
        assert session.binding_name_to_value["conversation_input"] == "changed"
        assert session.binding_name_to_value["action_result"] == 20
        assert "_result" not in session.binding_name_to_value
    finally:
        session.close()


def test_type_validation_matches_an_ordinary_natural_block() -> None:
    session = session_manager(
        StepCandidate(binding_name_to_value={"value": "10"}), StepCandidate(binding_name_to_value={"result": ConversationResult(()), "value": "10"})
    ).create_session()
    source = '''@nh.function
def ordinary() -> int:
    value: int = 1
    """execute
    Assign 10 to <:value>.
    """
    return value
expected = ordinary()
value: int = 1
'''
    try:
        assert session.submit(CellInput(source, mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("Assign 10 to <:value>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["value"] == session.binding_name_to_value["expected"] == 10
    finally:
        session.close()


def test_explicit_namespace_preserves_reference_identity_without_exposing_implementation() -> None:
    original = [1]

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            assert "_current_bindings" not in step_context.environment.python_name_to_value
            assert "bindings" not in step_context.step_locals
            assert step_context.step_locals["_original"] is original
            assert step_context.step_locals["ConversationResult"] == 5
            assert step_context.step_locals["_ConversationResult"] is ConversationResult
            await assign_expression_async(step_context, "temporary", "_original")
            assert await evaluate_expression_async(step_context, "temporary") is original
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": ConversationResult(()), "alias": original})

    session = SessionManager(
        runner=Runner(Executor(), PerimeterPolicy.NONE, lambda identity: bounded_resource_allocation(), accepts_aggregate_executor_accounting=True)
    ).create_session()
    try:
        session.binding_name_to_value.update(_original=original, ConversationResult=5)
        outcome = session.submit(CellInput("Read <ConversationResult> and assign <_original> to <:alias>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["alias"] is original
        assert "temporary" not in session.binding_name_to_value
        assert "_ConversationResult" not in session.binding_name_to_value
    finally:
        session.close()


def test_operator_lambda_in_lazy_expression_keeps_ordinary_python_behavior() -> None:
    session = session_manager().create_session()
    try:
        assert session.submit(CellInput("functions = (lambda value: value + 10 for _ in range(1))", mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("next(functions)(10)", mode="python")).wait()
        assert outcome.status == "success", messages(outcome)
        assert outcome.output_tuple[0].text == "20"
    finally:
        session.close()


def test_omitted_conversation_result_does_not_publish_or_silently_succeed() -> None:
    session = session_manager(StepCandidate(binding_name_to_value={"value": 10})).create_session()
    try:
        outcome = session.submit(CellInput("Assign 10 to <:value> and report it.")).wait()
        assert outcome.status == "failure"
        assert "value" not in session.binding_name_to_value
        assert not any(output.kind == "publication" for output in outcome.output_tuple)
    finally:
        session.close()


@pytest.mark.parametrize("future_annotations", [False, True])
def test_annotation_contracts_survive_later_cells_and_ignore_unselected_forward_references(future_annotations: bool) -> None:
    session = session_manager(StepCandidate(binding_name_to_value={"result": ConversationResult(()), "value": "10"})).create_session()
    try:
        prefix = "from __future__ import annotations\n" if future_annotations else ""
        assert session.submit(CellInput(prefix + "value: int", mode="python")).wait().status == "success"
        assert session.submit(CellInput("unselected: 'UnavailableType'", mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("Assign 10 to <:value>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["value"] == 10
        assert type(session.binding_name_to_value["value"]) is int
    finally:
        session.close()


@pytest.mark.parametrize("future_annotations", [False, True])
def test_annotation_redeclaration_survives_a_later_cell_failure(future_annotations: bool) -> None:
    session = session_manager(StepCandidate(binding_name_to_value={"result": ConversationResult(()), "value": "text"})).create_session()
    try:
        prefix = "from __future__ import annotations\n" if future_annotations else ""
        assert session.submit(CellInput(prefix + "value: int = 1", mode="python")).wait().status == "success"
        assert session.submit(CellInput("value: object\nraise ValueError('after declaration')", mode="python")).wait().status == "failure"
        outcome = session.submit(CellInput("Assign text to <:value>.")).wait()
        assert outcome.status == "success", messages(outcome)
        assert session.binding_name_to_value["value"] == "text"
    finally:
        session.close()
