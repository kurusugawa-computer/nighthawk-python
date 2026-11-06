from examples.async_object_workflow import SampleBatch
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.proposals import CodeProposal
from nighthawk.parts.evidence import EvidenceProjection
from nighthawk.parts.learning import LearningResult
from tests.support.learning import lesson

ANALYSIS_SOURCE = "async def analyze(samples: tuple[int, ...], criterion: int) -> tuple[tuple[int, int], ...]:\n    return tuple((index, value) for index, value in enumerate(samples) if value < criterion)\n"


def sample_batches() -> tuple[SampleBatch, ...]:
    return (
        SampleBatch(batch_id="first", sample_tuple=(1, -2)),
        SampleBatch(batch_id="second", sample_tuple=(-3, 0)),
        SampleBatch(batch_id="third", sample_tuple=(4, -5)),
    )


def analysis_executor(count: int) -> ScriptedExecutor:
    return ScriptedExecutor(
        responses=[
            StepCandidate(binding_name_to_value={"proposal": CodeProposal(source=ANALYSIS_SOURCE, export_name_tuple=("analyze",))})
            for _ in range(count)
        ]
    )


def learning_handler(call: StepRequest) -> StepCandidate:
    if "result" in call.output_binding_names:
        evidence = call.step_context.step_locals["evidence"]
        assert isinstance(evidence, EvidenceProjection)
        assert '"matches_criterion":false' in evidence.evidence_tuple[0].excerpt
        return StepCandidate(binding_name_to_value={"result": LearningResult(lesson_tuple=(lesson(evidence.evidence_tuple[0].reference),))})
    assert call.step_context.step_locals["lessons"]
    return StepCandidate(
        binding_name_to_value={
            "proposal": CodeProposal(
                source="def find_negatives(samples: tuple[int, ...]) -> tuple[int, ...]:\n    return tuple(value for value in samples if value < 0)\n",
                export_name_tuple=("find_negatives",),
            )
        }
    )
