import re
import runpy
from pathlib import Path

import pytest

import nighthawk as nh
from nighthawk.testing import ScriptedExecutor, pass_response, return_response


@pytest.fixture
def summary_assessment(tmp_path: Path):
    document = (Path(__file__).resolve().parents[2] / "docs" / "decisions.md").read_text()
    examples = re.findall(
        r"<!-- decision-example:summary_assessment -->\s*```python\n(.*?)```\s*<!-- /decision-example:summary_assessment -->",
        document,
        re.DOTALL,
    )
    assert len(examples) == 1
    source_path = tmp_path / "summary_assessment.py"
    source_path.write_text(examples[0])
    return runpy.run_path(str(source_path))["summarize_and_assess"]


@pytest.mark.asyncio
async def test_documented_generation_is_followed_by_a_decision(summary_assessment) -> None:
    document = "The Willow team completed 12 tickets."
    summary = "Willow completed 12 tickets."
    executor = ScriptedExecutor(
        [
            pass_response(binding_name_to_value={"claims": [document], "summary": summary}),
            pass_response(binding_name_to_value={"support_probability": 0.9}),
        ]
    )
    events: list[nh.lifecycle.StepFinished] = []
    with nh.run(executor), nh.scope(lifecycle=nh.lifecycle.StepLifecycle(events.append)):
        assert await summary_assessment(document) == (summary, 0.9)

    assert [call.block_kind for call in executor.calls] == ["execute", "decide"]
    assert executor.calls[1].input_binding_name_to_value == {"document": document, "summary": summary}
    assert len(events) == 2 and all(isinstance(event, nh.lifecycle.StepCompleted) for event in events)
    assert [event.block_kind for event in events] == ["execute", "decide"]


@pytest.mark.asyncio
async def test_documented_generation_cannot_return_an_unassessed_result(summary_assessment) -> None:
    executor = ScriptedExecutor([return_response(("Unchecked summary", 0.99))])
    with nh.run(executor), pytest.raises(nh.ExecutionError, match="not allowed") as caught:
        await summary_assessment("The Willow team completed 12 tickets.")
    assert len(executor.calls) == 1
    failure = caught.value.step_failed
    assert failure is not None and failure.failure_stage == "outcome_validation"
    assert failure.assigned_binding_name_to_value == {}
