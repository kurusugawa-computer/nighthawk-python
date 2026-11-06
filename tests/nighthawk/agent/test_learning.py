"""Bootstrap limits and reference membership preserve one typed judgment boundary."""

from dataclasses import dataclass

import pytest

from nighthawk.agent import CodeProposal, RunFailedError
from nighthawk.agent.history import EvidenceReference, HistoryEvidence, HistoryProjection
from nighthawk.agent.learning import ImprovementCandidate, LearningResult, Lesson, derive_learning, format_candidate_review
from nighthawk.runtime.step_request import StepCandidate, StepRequest
from nighthawk.testing import CallbackExecutor, ScriptedExecutor

from .conftest import make_runner
from .support import EchoInput, EchoResult, run_body


def evidence() -> HistoryProjection:
    return HistoryProjection(
        run_id="run",
        horizon_sequence=1,
        evidence_tuple=(
            HistoryEvidence(
                reference=EvidenceReference(run_id="run", event_id="observed"),
                sequence=1,
                excerpt="Observed (-2, 0); expected (-2,).",
                incomplete=False,
            ),
        ),
        next_starting_sequence=None,
        incomplete=False,
    )


def lesson(reference: EvidenceReference) -> Lesson:
    return Lesson(
        observation="Zero was incorrectly included.",
        applicability="Strict negative selection",
        evidence_reference_tuple=(reference,),
        next_verification_step="Verify zero is excluded.",
    )


@pytest.mark.parametrize("invalid", [False, True])
def test_learning_checks_only_membership_of_explicit_inputs(invalid: bool) -> None:
    supplied = evidence()
    reference = EvidenceReference(run_id="wrong-run", event_id="observed") if invalid else supplied.evidence_tuple[0].reference
    output = LearningResult(lesson_tuple=(lesson(reference),))

    def body(run_input: EchoInput) -> EchoResult:
        result = derive_learning(supplied, ())
        assert result == output
        return EchoResult()

    if invalid:
        with pytest.raises(RunFailedError) as failure:
            run_body(body, runner=make_runner(), step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": output})]))
        assert isinstance(failure.value.__cause__, ValueError)
    else:
        run_body(body, runner=make_runner(), step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": output})]))


def test_empty_output_and_current_lesson_evidence_are_allowed() -> None:
    supplied = evidence()
    older = lesson(EvidenceReference(run_id="earlier", event_id="earlier-event"))

    def body(run_input: EchoInput) -> EchoResult:
        assert derive_learning(supplied, ()) == LearningResult()
        assert derive_learning(supplied, (older,)).lesson_tuple == (older,)
        return EchoResult()

    run_body(
        body,
        runner=make_runner(),
        step_executor=ScriptedExecutor(
            responses=[
                StepCandidate(binding_name_to_value={"result": LearningResult()}),
                StepCandidate(binding_name_to_value={"result": LearningResult(lesson_tuple=(older,))}),
            ]
        ),
    )


def test_bootstrap_collection_text_and_candidate_source_bounds() -> None:
    item = lesson(evidence().evidence_tuple[0].reference)
    with pytest.raises(ValueError):
        LearningResult(lesson_tuple=(item,) * 9)
    with pytest.raises(ValueError):
        Lesson(observation="x" * 2001, applicability="", evidence_reference_tuple=(), next_verification_step="")
    with pytest.raises(ValueError):
        Lesson(observation="", applicability="", evidence_reference_tuple=item.evidence_reference_tuple * 9, next_verification_step="")
    proposal = CodeProposal(source="#" * 20001, export_name_tuple=("candidate",))
    with pytest.raises(ValueError, match="20000"):
        ImprovementCandidate(proposal=proposal, intended_behavior="", evidence_reference_tuple=(), validation_description="")
    assert len(proposal.source) == 20001  # The global CodeProposal contract is unchanged.


def test_caller_defined_state_and_inert_review_material_need_no_registration() -> None:
    @dataclass
    class Notebook:
        selected_lessons: tuple[Lesson, ...] = ()

    notebook = Notebook()
    supplied = evidence()
    candidate = ImprovementCandidate(
        proposal=CodeProposal(source="def choose() -> int:\n    return -2\n", export_name_tuple=("choose",)),
        intended_behavior="Choose a negative",
        evidence_reference_tuple=(supplied.evidence_tuple[0].reference,),
        validation_description="Check -2",
    )
    output = LearningResult(lesson_tuple=(lesson(supplied.evidence_tuple[0].reference),), candidate_tuple=(candidate,))
    trusted_programs = []

    def handler(call: StepRequest) -> StepCandidate:
        trusted_programs.append(call.processed_natural_program)
        assert call.step_context.step_locals["current_lessons"] == notebook.selected_lessons
        return StepCandidate(binding_name_to_value={"result": output})

    def body(run_input: EchoInput) -> EchoResult:
        notebook.selected_lessons = derive_learning(supplied, notebook.selected_lessons).lesson_tuple
        notebook.selected_lessons = derive_learning(supplied, notebook.selected_lessons).lesson_tuple
        return EchoResult()

    run_body(body, runner=make_runner(), step_executor=CallbackExecutor(handler))
    assert trusted_programs[0] == trusted_programs[1]
    review = format_candidate_review(candidate, evidence=supplied.evidence_tuple, observed_checks="Not executed")
    assert candidate.proposal.source in review and supplied.evidence_tuple[0].excerpt in review
    assert "Not executed" in review and "not ratified" in review
