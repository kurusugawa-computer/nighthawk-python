"""Synthetic documents and claims contain no external or private data."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated

import pytest
from pydantic import Field
from pydantic_ai.messages import UserContent

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.context import PROGRAM_CACHE_BOUNDARY, ContextRequest, DefaultContextBuilder
from nighthawk.core.lifecycle import StepCompleted, StepFinished, StepLifecycle, StepStarted
from nighthawk.core.step_outcome import Return
from tests.support.model_evaluation import ModelEvaluation


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
    evaluation: ModelEvaluation
    requests: list[ContextRequest] = field(default_factory=list)
    task_name_to_value_list: list[dict[str, object]] = field(default_factory=list)
    events: list[StepStarted | StepFinished] = field(default_factory=list)

    def build(self, request: ContextRequest) -> Sequence[UserContent]:
        self.requests.append(request)
        self.task_name_to_value_list.append(dict(request.environment.task_name_to_value))
        return DefaultContextBuilder().build(request)

    def finished(self, event: StepFinished) -> None:
        self.events.append(event)
        self.evaluation.steps.append(event)

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


def completed_value(event: StepCompleted, binding_name: str) -> object:
    return event.outcome.value if isinstance(event.outcome, Return) else event.assigned_binding_name_to_value[binding_name]


@pytest.mark.asyncio
async def test_python_iteration_assesses_each_generated_summary_line(model_evaluation: ModelEvaluation) -> None:
    document = "The Willow team completed 12 tickets. The Birch team completed 18 tickets."
    observer = CompositionObserver(model_evaluation)
    with (
        nh.scope(
            context_builder=observer,
            lifecycle=StepLifecycle(on_step_started=observer.events.append, on_step_finished=observer.finished),
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
async def test_model_selected_children_receive_explicit_review_policy(model_evaluation: ModelEvaluation) -> None:
    document = (
        "Twenty volunteers received an experimental treatment. "
        "Their average symptom score declined afterwards, but there was no control group. "
        "The report does not establish that the treatment caused the improvement."
    )
    supported_claim = "Twenty volunteers received the treatment."
    unsupported_claim = "The treatment caused the improvement."
    excluded_claim = "The report has a short title."
    observer = CompositionObserver(model_evaluation)
    with (
        nh.scope(
            context_builder=observer,
            lifecycle=StepLifecycle(on_step_started=observer.events.append, on_step_finished=observer.finished),
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
async def test_model_materializes_parent_counter_before_ten_real_children(model_evaluation: ModelEvaluation) -> None:
    observer = CompositionObserver(model_evaluation)
    with (
        nh.scope(
            context_builder=observer,
            lifecycle=StepLifecycle(on_step_started=observer.events.append, on_step_finished=observer.finished),
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


_ROUTING_TEAM_TO_GUIDANCE = {
    "billing": (
        "Invoices, refunds, duplicate or unexpected charges on a card, payment methods, tax documents, and currency questions.",
        "A customer was charged twice for one invoice and asks for the duplicate back.",
        "Contract wording goes to legal; a failed product that caused the charge still stays here.",
    ),
    "accounts": (
        "Individual sign-in problems, password resets, two-factor recovery, profile names, email address changes, and closing a personal account.",
        "A user lost the phone with their authenticator app and cannot sign in.",
        "Company-wide sign-in through a single sign-on provider goes to identity.",
    ),
    "identity": (
        "Single sign-on, directory synchronization, company-wide access policies, provisioning and removing employee seats, and role mappings.",
        "An administrator reports that new employees no longer appear after the directory sync.",
        "One person's forgotten password goes to accounts.",
    ),
    "operations": (
        "Service outages, error pages for many users at once, degraded performance across a region, incident updates, and status page questions.",
        "Every page returns a 503 error for all users in one region.",
        "A crash in one mobile application goes to mobile.",
    ),
    "mobile": (
        "Crashes, freezes, layout problems, offline mode, and push notifications in the iOS and Android applications.",
        "The Android application closes as soon as the calendar tab opens.",
        "Email digests go to notifications.",
    ),
    "desktop": (
        "Installers, automatic updates, crashes, and system tray behavior of the Windows and macOS desktop applications.",
        "The macOS application fails to update and shows the old version.",
        "Problems that appear only in the web browser go to the team that owns the feature.",
    ),
    "integrations": (
        "Connections to calendars, chat tools, and storage providers, including webhooks that stop firing and expired connector authorizations.",
        "Meetings from the calendar connector stopped appearing yesterday.",
        "Developer API tokens and rate limits go to api.",
    ),
    "data_export": (
        "Bulk exports, scheduled reports, CSV and spreadsheet downloads, and the retention period of exported files.",
        "A scheduled weekly CSV report arrives empty.",
        "Requests to delete personal data go to privacy.",
    ),
    "privacy": (
        "Requests to delete or access personal data, consent records, cookie preferences, and regulatory inquiries about personal data.",
        "A person asks the company to erase all personal data it holds about them.",
        "Suspected break-ins and leaked passwords go to security.",
    ),
    "security": (
        "Suspected account takeover, phishing that imitates the product, vulnerability reports, leaked credentials, and unusual sign-in alerts.",
        "A customer received an email that copies the product's login page and asks for a password.",
        "Consent and data deletion requests go to privacy.",
    ),
    "shipping": (
        "Physical deliveries, tracking numbers, damaged or missing parcels, delivery addresses, and customs forms for hardware orders.",
        "The tracking number for a card reader order has not updated for a week.",
        "A delivered device that does not work goes to hardware.",
    ),
    "hardware": (
        "Faulty card readers, scanners, docking stations, and their firmware, including warranty replacements and returns.",
        "A docking station stops charging the laptop after a firmware update.",
        "A parcel lost in transit goes to shipping.",
    ),
    "onboarding": (
        "First-week setup questions, guided tours, sample projects, importing initial data, and choosing an initial plan.",
        "A new team asks how to set up its first project and invite members.",
        "Training courses and certification go to education.",
    ),
    "education": (
        "Training courses, certification exams, webinars, learning paths, and classroom licenses for schools and universities.",
        "A teacher asks how many students one classroom license covers.",
        "Reseller programs go to partnerships.",
    ),
    "partnerships": (
        "Reseller agreements, referral commissions, marketplace listings, and co-marketing requests from other companies.",
        "An agency asks to join the reseller program and earn referral fees.",
        "Contract disputes with customers go to legal.",
    ),
    "localization": (
        "Missing or wrong translations, date, time, and number formats, time zones in the interface, and right-to-left layouts.",
        "Dates appear in the wrong order for users in Germany.",
        "Screen reader problems go to accessibility.",
    ),
    "accessibility": (
        "Screen reader support, keyboard navigation, focus order, color contrast, text scaling, and captions on product videos.",
        "A screen reader skips the buttons in the export dialog.",
        "Wrong translations go to localization.",
    ),
    "api": (
        "Rate limits, authentication tokens for developers, client libraries, pagination, and unexpected API responses or status codes.",
        "A developer receives 429 responses far below the documented rate limit.",
        "Ready-made connectors to other products go to integrations.",
    ),
    "search": (
        "Search results that miss documents, slow searches, filters, sorting, saved searches, and search inside attachments.",
        "Searching for a project name does not return the project created this morning.",
        "Dashboards and metric definitions go to analytics.",
    ),
    "notifications": (
        "Email digests, alert rules, notification frequency, quiet hours, and unsubscribe links, except mobile push notifications.",
        "A user receives the daily digest three times a day.",
        "Push notifications on phones go to mobile.",
    ),
    "storage": (
        "Quota limits, attachment upload failures, file previews, version history of files, and recovering deleted files.",
        "Uploading a 40 MB attachment fails although the quota is not reached.",
        "Exports of many records go to data_export.",
    ),
    "analytics": (
        "Dashboards, charts, metric definitions, differences between reported numbers, and scheduled dashboard snapshots.",
        "The revenue chart shows a different total than the monthly report.",
        "Search result quality goes to search.",
    ),
    "legal": (
        "Contract terms, data processing agreements, subpoenas, trademark complaints, and requests for signed compliance documents.",
        "A customer's lawyer asks for changes to the data processing agreement.",
        "Refund amounts go to billing.",
    ),
    "feedback": (
        "Feature requests, product suggestions, survey invitations, and praise that need no fix or action from another team.",
        "A user suggests adding a dark theme to the editor.",
        "Anything that is broken goes to the team that owns it.",
    ),
}
_ROUTING_RULES = "\n    ".join(
    f"- {team}: {responsibility} Example: {example} Not here: {not_here}"
    for team, (responsibility, example, not_here) in _ROUTING_TEAM_TO_GUIDANCE.items()
)


@nh.function
async def route_ticket(ticket: str) -> str:
    team: str = ""
    f"""execute
    Route each support ticket to exactly one team. The teams and their responsibilities are:
    {_ROUTING_RULES}
    {PROGRAM_CACHE_BOUNDARY}
    Read <ticket> and set <:team> to the name of the one responsible team, spelled exactly as listed.
    """
    return team


@pytest.mark.asyncio
async def test_repeated_calls_route_each_ticket_with_one_stable_program(model_evaluation: ModelEvaluation) -> None:
    # A fresh reference per trial keeps requests from repeating earlier trials, so only the program prefix can be reused.
    reference = secrets.token_hex(4)
    ticket_to_team = {
        f"Case {reference}-1: My card was charged twice for the March invoice; please refund the duplicate.": "billing",
        f"Case {reference}-2: The Android app crashes as soon as I open the calendar tab.": "mobile",
        f"Case {reference}-3: Please erase all personal data you hold about me under the privacy law.": "privacy",
        f"Case {reference}-4: Every page of the dashboard shows a 503 error for all of our users in Europe.": "operations",
    }
    team_list: list[str] = []
    model_evaluation.name_to_observation.update(team_list=team_list)
    async with asyncio.timeout(360):
        for ticket in ticket_to_team:
            team_list.append(await route_ticket(ticket))
    assert [team.strip().casefold() for team in team_list] == list(ticket_to_team.values())
