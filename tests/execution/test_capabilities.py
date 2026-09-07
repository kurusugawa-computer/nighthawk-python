from __future__ import annotations

import contextlib
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability, Hooks
from pydantic_ai.models.test import TestModel

import nighthawk as nh
from nighthawk.errors import NighthawkError
from nighthawk.runtime.step_context import StepContext
from tests.execution.stub_executor import StubExecutor


class _RecordingAgent:
    def __init__(self) -> None:
        self.received_keyword_arguments: list[dict[str, Any]] = []

    def run_sync(self, *args: object, **kwargs: Any) -> object:
        _ = args
        self.received_keyword_arguments.append(kwargs)

        class Result:
            output = {"result": {"kind": "pass"}}

        return Result()


class _MarkerCapability(AbstractCapability[StepContext]):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label


def test_get_capabilities_requires_active_run() -> None:
    with pytest.raises(NighthawkError):
        nh.get_capabilities()


def test_scope_capabilities_inherit_appends_and_replace_substitutes() -> None:
    first = _MarkerCapability("first")
    second = _MarkerCapability("second")
    third = _MarkerCapability("third")

    with nh.run(StubExecutor()):
        assert nh.get_capabilities() == ()

        with nh.scope(capabilities=[first]):
            assert nh.get_capabilities() == (first,)

            with nh.scope(capabilities=nh.Extend([second])):
                assert nh.get_capabilities() == (first, second)

            with nh.scope(capabilities=[third]):
                assert nh.get_capabilities() == (third,)

            with nh.scope(capabilities=[]):
                assert nh.get_capabilities() == ()

            with nh.scope(capabilities=nh.UNSET):
                assert nh.get_capabilities() == (first,)

        assert nh.get_capabilities() == ()


def test_agent_step_executor_passes_scoped_capabilities_to_agent_run() -> None:
    recording_agent = _RecordingAgent()
    step_executor = nh.AgentStepExecutor.from_agent(agent=recording_agent)
    capability = _MarkerCapability("scoped")

    @nh.natural_function
    def natural_pass_function() -> None:
        """natural
        Do nothing.
        """

    with nh.run(step_executor):
        natural_pass_function()
        with nh.scope(capabilities=[capability]):
            natural_pass_function()

    assert recording_agent.received_keyword_arguments[0]["capabilities"] == []
    assert recording_agent.received_keyword_arguments[1]["capabilities"] == [capability]


def test_before_model_request_hook_fires_for_natural_block_on_test_model() -> None:
    request_count = 0

    async def count_request(context: Any, request_context: Any) -> Any:
        nonlocal request_count
        _ = context
        request_count += 1
        return request_context

    agent: Agent[StepContext, Any] = Agent(TestModel(), deps_type=StepContext)
    step_executor = nh.AgentStepExecutor.from_agent(agent=agent)

    @nh.natural_function
    def natural_pass_function() -> None:
        """natural
        Do nothing.
        """

    with (
        nh.run(step_executor),
        nh.scope(capabilities=[Hooks(before_model_request=count_request)]),
        contextlib.suppress(nh.ExecutionError),
    ):
        natural_pass_function()

    assert request_count >= 1
