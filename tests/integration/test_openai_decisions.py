from typing import Annotated

import pytest
from pydantic import Field

import nighthawk as nh
from tests.integration.skip_helpers import requires_openai_integration


@nh.function
async def assess_support(evidence: str, claim: str) -> float:
    probability: Annotated[float, Field(ge=0, le=1)] = 0.0
    """decide <:probability>
    Does <evidence> support <claim>?
    """
    return probability


@pytest.mark.asyncio
async def test_live_openai_decision_preserves_probability_and_usage() -> None:
    requires_openai_integration()
    records: list[nh.lifecycle.StepFinished] = []
    with nh.run(decision_model="openai-decisions:gpt-6-luna"), nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=records.append)):
        probability = await assess_support("The meeting starts at 09:00 UTC.", "The meeting starts at 09:00 UTC.")
        assert 0 <= probability <= 1
        assert nh.get_usage_meter().total_tokens > 0
    assert len(records) == 1 and isinstance(records[0], nh.lifecycle.StepCompleted)
    assert records[0].block_kind == "decide"
    assert records[0].model_result is not None
    assert records[0].model_result.output == probability
