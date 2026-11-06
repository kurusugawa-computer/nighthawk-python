from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

import pytest
from pydantic import AfterValidator, ValidationError

import nighthawk as nh
from nighthawk.core import oversight
from nighthawk.core.errors import ExecutionError
from nighthawk.core.lifecycle import StepFailed, StepFinished, StepLifecycle
from nighthawk.core.oversight import AbortStep, Accept, Oversight, Rewrite
from nighthawk.core.step_definition import StepDefinition
from nighthawk.core.step_outcome import Pass, StepOutcome
from nighthawk.core.step_request import StepCandidate
from tests.support.executors import InspectingExecutor


@pytest.mark.parametrize("rewrite", [False, True])
@pytest.mark.asyncio
async def test_initialized_output_finalization_and_empty_rewrite(execute: Callable[..., Awaitable[StepOutcome]], rewrite: bool) -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []
    inspections: list[object] = []

    def inspect(commit: oversight.StepCommit) -> Accept | Rewrite:
        inspections.append(dict(commit.binding_name_to_value))
        return Rewrite(binding_name_to_value={}) if rewrite else Accept()

    with (
        nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=Pass()))),
        nh.scope(oversight=Oversight(inspect_step_commit=inspect), lifecycle=StepLifecycle(on_step_finished=events.append)),
    ):
        await execute(
            StepDefinition(
                "normalize", output_binding_names=("result", "unbound"), binding_name_to_type={"result": Annotated[str, AfterValidator(str.strip)]}
            ),
            name_to_value={"result": " draft "},
            write_binding=saved_name_to_value.__setitem__,
        )
    assert inspections == [{"result": "draft"}]
    assert saved_name_to_value == ({} if rewrite else {"result": "draft"})
    assert events[0].assigned_binding_name_to_value == saved_name_to_value


@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.asyncio
async def test_validation_and_commit_rejection_never_write(execute: Callable[..., Awaitable[StepOutcome]], reject: bool) -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []
    with (
        nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=Pass(), binding_name_to_value={"result": 1 if reject else "bad"}))),
        nh.scope(
            oversight=Oversight(inspect_step_commit=lambda commit: AbortStep("refused")), lifecycle=StepLifecycle(on_step_finished=events.append)
        ),
        pytest.raises(oversight.StepAbortedError if reject else ExecutionError),
    ):
        await execute(
            StepDefinition("write", output_binding_names=("result",)), name_to_value={"result": 0}, write_binding=saved_name_to_value.__setitem__
        )
    assert saved_name_to_value == {}
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == ("oversight_abort" if reject else "binding_validation")
    if not reject:
        assert isinstance(events[0].original_exception, ValidationError)
