"""The optional reference loop is a fixed composition over the shared Harness."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from nighthawk.agent import CodeProposal, DefinitionMaterializationEvent, GeneratedCallableReference, RunFailedError, RunHandle
from nighthawk.agent.reference import (
    MAXIMUM_NOTE_CHARACTER_COUNT,
    ReferenceLoopInput,
    ReferenceLoopResult,
    RetainedNote,
    build_reference_loop_composition,
    reference_loop,
    run_reference_loop,
)
from nighthawk.runtime.step_request import StepCandidate
from nighthawk.testing import ScriptedExecutor

from .conftest import make_runner

_DOUBLE_SOURCE = "def double(x: int) -> int:\n    return x * 2\n"


def test_loop_dispatches_a_proposed_definition_and_returns_the_final_summary() -> None:
    executor = ScriptedExecutor(
        responses=[
            StepCandidate(binding_name_to_value={"code_proposal": CodeProposal(source=_DOUBLE_SOURCE, export_name_tuple=("double",))}),
            StepCandidate(binding_name_to_value={"final_summary": "doubled"}),
        ]
    )
    handle = RunHandle()
    result = run_reference_loop("double things", handle=handle, runner=replace(make_runner(), step_executor=executor))
    assert result == ReferenceLoopResult(summary="doubled", generated_function_name_tuple=("double",))
    assert executor.requests[0].step_context.step_locals["note"] is None
    second_note = executor.requests[1].step_context.step_locals["note"]
    assert isinstance(second_note, RetainedNote)
    assert second_note.text == "Defined generated functions: double."
    references = cast(tuple[GeneratedCallableReference, ...], executor.requests[1].step_context.step_locals["generated_function_tuple"])
    assert [reference.definition.export_name for reference in references] == ["double"]
    materializations = [entry.event for entry in handle.ledger.select(DefinitionMaterializationEvent)]
    assert [event.source_provenance_class for event in materializations] == ["ratified", "generated"]  # type: ignore[union-attr]
    assert materializations[0].export_name == "reference_loop"  # type: ignore[union-attr]


def test_loop_retains_a_bounded_note_and_reports_rejected_dispatches() -> None:
    long_note = "n" * (MAXIMUM_NOTE_CHARACTER_COUNT + 10)
    executor = ScriptedExecutor(
        responses=[
            StepCandidate(binding_name_to_value={"next_note": long_note}),
            StepCandidate(
                binding_name_to_value={"code_proposal": CodeProposal(source="def f() -> int:\n    return missing()\n", export_name_tuple=("f",))}
            ),
            StepCandidate(),
            StepCandidate(binding_name_to_value={"final_summary": "finished"}),
        ]
    )
    result = run_reference_loop("task", runner=replace(make_runner(), step_executor=executor))
    assert result.summary == "finished"
    truncated = executor.requests[1].step_context.step_locals["note"]
    assert isinstance(truncated, RetainedNote)
    assert truncated.truncated is True
    assert len(truncated.text) == MAXIMUM_NOTE_CHARACTER_COUNT
    rejected = executor.requests[2].step_context.step_locals["note"]
    assert isinstance(rejected, RetainedNote)
    assert rejected.text.startswith("Dispatch failed: DispatchRejectedError")
    assert "undeclared_dependency" in rejected.text
    idle = executor.requests[3].step_context.step_locals["note"]
    assert isinstance(idle, RetainedNote)
    assert idle.text.startswith("Expected exactly one transition; received 0.")


@pytest.mark.parametrize(
    "selected_names",
    [
        ("code_proposal", "next_note"),
        ("code_proposal", "final_summary"),
        ("next_note", "final_summary"),
        ("code_proposal", "next_note", "final_summary"),
    ],
)
def test_ambiguous_transition_neither_dispatches_nor_accepts_completion(selected_names: tuple[str, ...]) -> None:
    binding_name_to_value = {
        "code_proposal": CodeProposal(source=_DOUBLE_SOURCE, export_name_tuple=("double",)),
        "next_note": "ambiguous note",
        "final_summary": "premature completion",
    }
    executor = ScriptedExecutor(
        responses=[
            StepCandidate(binding_name_to_value={name: binding_name_to_value[name] for name in selected_names}),
            StepCandidate(binding_name_to_value={"final_summary": "resolved"}),
        ]
    )
    handle = RunHandle()
    result = run_reference_loop("task", runner=replace(make_runner(), step_executor=executor), handle=handle)
    assert result == ReferenceLoopResult(summary="resolved")
    assert len(executor.requests) == 2
    guidance = executor.requests[1].step_context.step_locals["note"]
    assert isinstance(guidance, RetainedNote)
    assert guidance.text.startswith(f"Expected exactly one transition; received {len(selected_names)}.")
    assert executor.requests[1].step_context.step_locals["generated_function_tuple"] == ()
    assert all(
        event.source_provenance_class == "ratified"
        for entry in handle.ledger.select(DefinitionMaterializationEvent)
        if isinstance(event := entry.event, DefinitionMaterializationEvent)
    )


def test_loop_composition_declares_its_contract_and_initial_definition() -> None:
    composition = build_reference_loop_composition()
    assert composition.run_contract.input_type is ReferenceLoopInput
    assert composition.run_contract.result_type is ReferenceLoopResult
    assert composition.function_definition_tuple == (reference_loop,)


def test_loop_result_is_validated_at_the_run_end_seam() -> None:
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"final_summary": ""})])
    with pytest.raises(RunFailedError) as captured:
        run_reference_loop("task", runner=replace(make_runner(), step_executor=executor))
    assert captured.value.terminal_kind == "uncaught_exception"
