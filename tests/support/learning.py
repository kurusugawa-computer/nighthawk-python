from nighthawk.harness.evidence import EvidenceReference
from nighthawk.parts.learning import Lesson


def lesson(reference: EvidenceReference) -> Lesson:
    return Lesson(
        observation="Zero was incorrectly included.",
        applicability="Strict negative selection",
        evidence_reference_tuple=(reference,),
        next_verification_step="Verify zero is excluded.",
    )
