"""Synthetic documents and claims contain no external or private data."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated

import pytest
from pydantic import Field
from pydantic_ai.messages import UserContent

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFinished, StepLifecycle, StepStarted
from nighthawk.runtime import ContextRequest, DefaultContextBuilder, Return
from tests.integration.skip_helpers import requires_openai_integration


@nh.function
async def assess_line(document: str, line: str) -> float:
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    """execute
    Assess how strongly <document> supports <line>.
    Set <:confidence> to a score from 0.0 to 1.0.
    Directly documented facts merit at least 0.8; unsupported claims merit at most 0.2.
    """
    return confidence


@nh.function
async def summarize_and_assess(document: str) -> list[tuple[str, float]]:
    summary: str = ""
    """execute
    ---
    deny: [return]
    ---
    Summarize <document> in <:summary>, with one factual claim per line.
    Produce exactly two nonempty lines, one for each named team and its completed ticket count.
    Preserve each team's name and numerical count, using decimal digits.
    """
    assessments: list[tuple[str, float]] = []
    for line in summary.splitlines():
        confidence = await assess_line(document, line)
        assessments.append((line, confidence))
    return assessments


@nh.function
async def assess_claim(document: str, claim: str) -> float:
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    """execute
    Assess how strongly <document> supports <claim>, applying <review_guidance>.
    Set <:confidence> to a score from 0.0 to 1.0.
    """
    return confidence


@nh.function
async def review_claims(document: str, claims: list[str]) -> list[tuple[str, float]]:
    review_guidance = (
        "Review numerical and causal claims only. "
        "Score a directly documented fact from 0.8 to 1.0. "
        "Score an unsupported causal assertion from 0.0 to 0.2. "
        "A before-and-after observation without a control group does not establish causation."
    )
    assessments: list[tuple[str, float]] = []
    with nh.scope(task_references={"review_guidance": review_guidance}):
        """execute
        Use <review_guidance> to choose claims from <claims> that need closer review.
        Call <assess_claim> with <document> and each selected claim exactly once, awaiting each result.
        Store each selected claim verbatim and its returned score in <:assessments>.
        Do not assess the excluded claims.
        """
    return assessments


@nh.function
async def block_two(i: int) -> str:
    message = ""
    """execute
    Inspect <i>, the explicitly supplied one-based iteration.
    Set <:message> to 'fifth' on iteration 5, 'eighth' on iteration 8,
    and 'ordinary' on every other iteration.
    """
    return message


@nh.function
async def block_one() -> list[str]:
    messages: list[str] = []
    """execute
    Call <block_two> ten times in sequence.
    Before each call, use nh_assign to set the temporary name i to the
    current one-based iteration, from 1 through 10.
    Pass i as the argument to block_two(i).
    Collect the returned strings in <:messages> in call order.
    """
    return messages


@dataclass
class CompositionObserver:
    requests: list[ContextRequest] = field(default_factory=list)
    task_name_to_value_list: list[dict[str, object]] = field(default_factory=list)
    events: list[StepStarted | StepFinished] = field(default_factory=list)

    def build(self, request: ContextRequest) -> Sequence[UserContent]:
        self.requests.append(request)
        self.task_name_to_value_list.append(dict(request.environment.task_name_to_value))
        return DefaultContextBuilder().build(request)

    def completed(self) -> list[StepCompleted]:
        completed = [event for event in self.events if isinstance(event, StepCompleted)]
        started = [event for event in self.events if isinstance(event, StepStarted)]
        assert len(self.events) == len(started) + len(completed), self.events
        assert len(started) == len(completed) == len(self.requests)
        started_identifiers = [event.execution_reference.step_execution_id for event in started]
        completed_identifiers = [event.execution_reference.step_execution_id for event in completed]
        assert len(set(started_identifiers)) == len(started_identifiers)
        assert set(started_identifiers) == set(completed_identifiers)
        for event in completed:
            reference = event.execution_reference
            own_start = next(item for item in started if item.execution_reference == reference)
            assert self.events.index(own_start) < self.events.index(event)
            if reference.parent_step_execution_id is not None:
                parent_start = next(item for item in started if item.execution_reference.step_execution_id == reference.parent_step_execution_id)
                parent_finish = next(item for item in completed if item.execution_reference.step_execution_id == reference.parent_step_execution_id)
                assert self.events.index(parent_start) < self.events.index(own_start) < self.events.index(event) < self.events.index(parent_finish)
        return completed


@pytest.fixture(params=["gpt-5.6-luna", "gpt-5.6-sol"])
def executor(request: pytest.FixtureRequest) -> nh.AgentStepExecutor:
    settings_class = requires_openai_integration()
    return nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            execution_model=f"openai-responses:{request.param}",
            execution_model_settings=settings_class(openai_reasoning_effort="medium", max_tokens=8192, timeout=90),
        )
    )


def completed_value(event: StepCompleted, binding_name: str) -> object:
    return event.outcome.value if isinstance(event.outcome, Return) else event.assigned_binding_name_to_value[binding_name]


@pytest.mark.asyncio
async def test_python_iteration_assesses_each_generated_summary_line(executor: nh.AgentStepExecutor) -> None:
    document = "The Willow team completed 12 tickets. The Birch team completed 18 tickets."
    observer = CompositionObserver()
    with (
        nh.run(executor),
        nh.scope(
            context_builder=observer,
            lifecycle=StepLifecycle(on_step_started=observer.events.append, on_step_finished=observer.events.append),
        ),
    ):
        async with asyncio.timeout(360):
            assessments = await summarize_and_assess(document)

    completed = observer.completed()
    assert len(completed) == 3
    assert all(event.execution_reference.parent_step_execution_id is None for event in completed)
    summary = completed_value(completed[0], "summary")
    assert isinstance(summary, str)
    lines = summary.splitlines()
    assert len(lines) == len(assessments) == 2
    assert all(line.strip() for line in lines)
    assert sum("Willow" in line and "12" in line for line in lines) == 1
    assert sum("Birch" in line and "18" in line for line in lines) == 1
    assert [line for line, _ in assessments] == lines
    for event, (line, confidence) in zip(completed[1:], assessments, strict=True):
        assert event.input_binding_name_to_value is not None
        assert event.input_binding_name_to_value["document"] == document
        assert event.input_binding_name_to_value["line"] == line
        assert confidence == completed_value(event, "confidence")
        assert 0.8 <= confidence <= 1.0


@pytest.mark.asyncio
async def test_model_selected_children_receive_explicit_review_policy(executor: nh.AgentStepExecutor) -> None:
    document = (
        "Twenty volunteers received an experimental treatment. "
        "Their average symptom score declined afterwards, but there was no control group. "
        "The report does not establish that the treatment caused the improvement."
    )
    supported_claim = "Twenty volunteers received the treatment."
    unsupported_claim = "The treatment caused the improvement."
    excluded_claim = "The report has a short title."
    observer = CompositionObserver()
    with (
        nh.run(executor),
        nh.scope(
            context_builder=observer,
            lifecycle=StepLifecycle(on_step_started=observer.events.append, on_step_finished=observer.events.append),
        ),
    ):
        async with asyncio.timeout(360):
            assessments = await review_claims(document, [supported_claim, excluded_claim, unsupported_claim])

    completed = observer.completed()
    assert len(completed) == 3
    assert len(assessments) == 2
    claim_to_confidence = dict(assessments)
    assert set(claim_to_confidence) == {supported_claim, unsupported_claim}
    assert 0.8 <= claim_to_confidence[supported_claim] <= 1.0
    assert 0.0 <= claim_to_confidence[unsupported_claim] <= 0.2
    parent_reference = completed[-1].execution_reference
    assert parent_reference.parent_step_execution_id is None
    assert completed[-1].processed_natural_program is not None
    observed_claims: list[str] = []
    for event in completed[:-1]:
        assert event.execution_reference.parent_step_execution_id == parent_reference.step_execution_id
        assert event.input_binding_name_to_value is not None
        assert event.input_binding_name_to_value["document"] == document
        claim = event.input_binding_name_to_value["claim"]
        assert isinstance(claim, str)
        observed_claims.append(claim)
        assert completed_value(event, "confidence") == claim_to_confidence[claim]
    assert sorted(observed_claims) == sorted(claim_to_confidence)
    parent_request = observer.requests[0]
    policy = parent_request.environment.resolve("review_guidance", source="python")
    for request, shared_name_to_value in zip(observer.requests[1:], observer.task_name_to_value_list[1:], strict=True):
        assert shared_name_to_value["review_guidance"] == policy
        assert request.environment.describe("review_guidance")["origin"] == "task"


@pytest.mark.asyncio
async def test_model_materializes_parent_counter_before_ten_real_children(executor: nh.AgentStepExecutor) -> None:
    observer = CompositionObserver()
    with (
        nh.run(executor),
        nh.scope(
            context_builder=observer,
            lifecycle=StepLifecycle(on_step_started=observer.events.append, on_step_finished=observer.events.append),
        ),
    ):
        async with asyncio.timeout(600):
            messages = await block_one()

    expected = ["ordinary"] * 4 + ["fifth"] + ["ordinary"] * 2 + ["eighth"] + ["ordinary"] * 2
    assert messages == expected
    completed = observer.completed()
    assert len(completed) == 11
    parent_reference = completed[-1].execution_reference
    assert parent_reference.parent_step_execution_id is None
    assert [request.environment.resolve("i", source="python") for request in observer.requests[1:]] == list(range(1, 11))
    for i, (request, event, expected_message) in enumerate(zip(observer.requests[1:], completed[:-1], expected, strict=True), start=1):
        assert event.execution_reference == request.execution_reference
        assert event.execution_reference.parent_step_execution_id == parent_reference.step_execution_id
        assert event.input_binding_name_to_value is not None
        assert event.input_binding_name_to_value["i"] == i
        assert request.environment.describe("i")["origin"] == "python"
        assert completed_value(event, "message") == expected_message
