"""Optional bounded bootstrap judgments derived explicitly from supplied evidence.

No hook, implicit history, source ratification, or mandatory state model is added.
Trying a candidate remains a separate host-mediated dispatch chosen by the caller.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

import nighthawk as nh
from nighthawk.harness.evidence import EvidenceReference
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.parts.evidence import EvidenceItem, EvidenceProjection

__all__ = ["ImprovementCandidate", "LearningResult", "Lesson", "derive_learning", "format_candidate_review"]


type Narrative = Annotated[str, Field(max_length=2000)]
type EvidenceReferenceTuple = Annotated[tuple[EvidenceReference, ...], Field(max_length=8)]


class Lesson(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation: Narrative
    applicability: Narrative
    evidence_reference_tuple: EvidenceReferenceTuple
    next_verification_step: Narrative


class ImprovementCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal: CodeProposal
    intended_behavior: Narrative
    evidence_reference_tuple: EvidenceReferenceTuple
    validation_description: Narrative

    @field_validator("proposal")
    @classmethod
    def _bound_candidate_source(cls, proposal: CodeProposal) -> CodeProposal:
        if len(proposal.source) > 20000:
            raise ValueError("Bootstrap candidate source exceeds 20000 characters")
        return proposal


class LearningResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lesson_tuple: Annotated[tuple[Lesson, ...], Field(max_length=8)] = ()
    candidate_tuple: Annotated[tuple[ImprovementCandidate, ...], Field(max_length=8)] = ()


LEARNING_CONSTRUCTOR_TUPLE = (Lesson, ImprovementCandidate, LearningResult, EvidenceReference, CodeProposal, DependencyDeclaration, EffectFootprint)


@nh.function
def derive_learning(evidence: EvidenceProjection, current_lessons: tuple[Lesson, ...]) -> LearningResult:
    """Return bounded judgments and check cited input membership once.

    Membership establishes the existence of supplied supporting data, not the
    semantic validity of a lesson. Evidence may be cited from the projection or
    explicitly supplied current lessons; no other run history is implicitly read.
    """
    result: LearningResult | None = None
    """execute
    ---
    deny: [raise, return, break, continue]
    ---
    Learn from the supplied <evidence> and explicitly selected <current_lessons>.
    Inspect every supplied evidence.evidence_tuple item and read its excerpt.
    A prompt preview may omit nested fields: explicitly inspect the excerpt values
    through an expression before deciding that useful evidence is absent.
    Use the constructors in <LEARNING_CONSTRUCTOR_TUPLE> to set <:result> to LearningResult.
    For useful supported observations, produce a Lesson stating the observation,
    applicability, cited evidence references, and the next verification step.
    Use the exact reference objects from the evidence entries or current lessons.
    If evidence records an actual incorrect result and its expected result, capture
    at least one concrete Lesson citing that evidence to guide the subsequent procedure.
    Optionally propose inert ImprovementCandidate values for explicit later review.
    Do not execute source or claim a candidate is ratified. Empty tuples are valid
    when no supported lesson or candidate is useful. Conclusions remain judgments.
    """
    if result is None:
        raise ValueError("Learning requires an explicit typed result")
    allowed_reference_tuple = tuple(item.reference for item in evidence.evidence_tuple) + tuple(
        reference for lesson in current_lessons for reference in lesson.evidence_reference_tuple
    )
    for item in (*result.lesson_tuple, *result.candidate_tuple):
        if any(reference not in allowed_reference_tuple for reference in item.evidence_reference_tuple):
            raise ValueError("Learning output cites evidence absent from the supplied inputs")
    return result


def format_candidate_review(candidate: ImprovementCandidate, *, evidence: tuple[EvidenceItem, ...], observed_checks: str) -> str:
    """Format inert review text with exact source, selected evidence, and checks.

    This performs no saving or execution. A citation without a supplied excerpt
    remains an explicit locator; it is not represented as reconstructed evidence.
    """
    reference_to_evidence = {item.reference: item for item in evidence}
    excerpt_list = []
    for reference in candidate.evidence_reference_tuple:
        selected = reference_to_evidence.get(reference)
        excerpt_list.append(
            reference.model_dump_json()
            + "\n"
            + (selected.excerpt if selected is not None else "Excerpt unavailable; resolve this evidence location explicitly.")
        )
    dependencies = "\n".join(dependency.model_dump_json(fallback=str) for dependency in candidate.proposal.dependency_tuple) or "None"
    return "\n\n".join(
        (
            "Inert generated source candidate; not ratified.",
            "Intended behavior\n" + candidate.intended_behavior,
            "Source (exact)\n" + candidate.proposal.source,
            "Dependencies\n" + dependencies,
            "Effects\n" + candidate.proposal.effect_footprint.model_dump_json(),
            "Validation description\n" + candidate.validation_description,
            "Observed checks\n" + observed_checks,
            "Selected evidence\n" + "\n\n".join(excerpt_list),
        )
    )
