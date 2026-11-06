"""An ordinary two-trial Python loop explicitly derives and consumes a lesson."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

import nighthawk as nh
from nighthawk.harness.context import current_capabilities, current_run_ledger
from nighthawk.harness.dispatch import GeneratedCallableReference, dispatch
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.run_contract import RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.parts.evidence import EvidenceBounds, EvidenceQuery, project_evidence
from nighthawk.parts.learning import LearningResult, Lesson, derive_learning


class LearningLoopInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sample_tuple: tuple[int, ...] = (-2, 0, 3)


class LearningTrial(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    selected_value_tuple: tuple[int, ...]
    expected_value_tuple: tuple[int, ...]
    matches_criterion: bool
    lesson_input_tuple: tuple[Lesson, ...]


class LearningLoopResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    trial_tuple: tuple[LearningTrial, ...]
    learning: LearningResult
    corrected_proposal: CodeProposal


def build_learning_validation() -> Grant:
    def validate_negatives(
        sample_tuple: tuple[int, ...], selected_value_tuple: tuple[int, ...], lesson_input_tuple: tuple[Lesson, ...]
    ) -> LearningTrial:
        """Record observed values and the exact expected result for value < 0."""
        expected = tuple(value for value in sample_tuple if value < 0)
        return LearningTrial(
            selected_value_tuple=selected_value_tuple,
            expected_value_tuple=expected,
            matches_criterion=selected_value_tuple == expected,
            lesson_input_tuple=lesson_input_tuple,
        )

    return Grant.of(
        Capability(name="validate_negatives", function=validate_negatives, authority_boundary="domain validation of supplied numeric values")
    )


@nh.function
def propose_corrected_negatives(lessons: tuple[Lesson, ...]) -> CodeProposal:
    proposal: CodeProposal | None = None
    """execute
    ---
    deny: [raise, return, break, continue]
    ---
    Use the explicitly supplied <lessons> as guidance for a later procedure.
    Set <:proposal> to <CodeProposal>(source=..., export_name_tuple=("find_negatives",)).
    Define only def find_negatives(samples: tuple[int, ...]) -> tuple[int, ...].
    It must select exactly the values satisfying value < 0 in input order.
    Apply the lesson about the observed earlier behavior, including the boundary
    value it identifies. Do not execute the source; the host will dispatch it.
    """
    if proposal is None:
        raise ValueError("A corrected proposal is required")
    return proposal


def learning_loop(run_input: LearningLoopInput) -> LearningLoopResult:
    """Call learning at the chosen loop point, with no implicit execution hook."""
    lessons: tuple[Lesson, ...] = ()
    learning = LearningResult()
    proposal = CodeProposal(
        source="def find_negatives(samples: tuple[int, ...]) -> tuple[int, ...]:\n    return tuple(value for value in samples if value <= 0)\n",
        export_name_tuple=("find_negatives",),
    )
    trial_list: list[LearningTrial] = []
    for iteration in range(2):
        if iteration:
            proposal = propose_corrected_negatives(lessons)
        function = dispatch(proposal).export_name_to_value["find_negatives"]
        assert isinstance(function, GeneratedCallableReference)
        selected = function(run_input.sample_tuple)
        trial = current_capabilities()["validate_negatives"](run_input.sample_tuple, selected, lessons)  # type: ignore[operator]
        assert isinstance(trial, LearningTrial)
        trial_list.append(trial)
        if not iteration:
            ledger = current_run_ledger()
            evidence = project_evidence(
                ledger.entries, EvidenceQuery(event_kind_tuple=("capability_invocation",)), run_id=ledger.run_id, bounds=EvidenceBounds()
            )
            learning = derive_learning(evidence, lessons)
            lessons = learning.lesson_tuple
    return LearningLoopResult(trial_tuple=tuple(trial_list), learning=learning, corrected_proposal=proposal)


def build_learning_loop_composition(*, run_entry_grant: Grant) -> ScaffoldComposition[LearningLoopInput, LearningLoopResult]:
    return ScaffoldComposition(
        run_contract=RunContract(input_type=LearningLoopInput, result_type=LearningLoopResult),
        body=learning_loop,
        run_entry_grant=run_entry_grant,
        composition_perimeter_policy=PerimeterPolicy.NONE,
        function_definition_tuple=(derive_learning, propose_corrected_negatives),
    )


def run_learning_loop(run_input: LearningLoopInput, *, runner: Runner, run_entry_grant: Grant, handle: RunHandle | None = None) -> LearningLoopResult:
    return runner.run(build_learning_loop_composition(run_entry_grant=run_entry_grant), run_input, handle=handle)
