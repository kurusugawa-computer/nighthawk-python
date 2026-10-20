from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic_ai.messages import BinaryContent, CachePoint, UserContent

import nighthawk as nh
from nighthawk.lifecycle import StepFailed, StepFinished, StepLifecycle
from nighthawk.runtime import ContextRequest, DefaultContextBuilder, assign_tool_async


@nh.function
async def workflow(payload: object) -> int:
    result: int = 0
    """execute
    Examine <payload> and produce <:result>.
    """
    return result


class RecordingAgent:
    def __init__(self) -> None:
        self.prompts: list[str | tuple[UserContent, ...]] = []

    async def run(self, user_prompt: str | tuple[UserContent, ...], *, deps: nh.StepContext, **arguments: Any) -> Any:
        self.prompts.append(user_prompt)
        await assign_tool_async(deps, "result", "7")

        class Result:
            output = {"result": {"kind": "pass"}}

        return Result()


class NamedBuilder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.requests: list[ContextRequest] = []

    def __bool__(self) -> bool:
        raise AssertionError("Builder selection must not use truthiness")

    def build(self, request: ContextRequest) -> Sequence[UserContent]:
        self.requests.append(request)
        return [self.name]


def test_custom_builder_receives_live_environment_contracts_and_budgets() -> None:
    original = object()
    injected = object()
    agent = RecordingAgent()
    configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(locals_max_tokens=17, globals_max_items=3),
        user_prompt_suffix_fragments=("configured suffix",),
    )
    executor = nh.AgentStepExecutor.from_agent(agent=agent, configuration=configuration)
    builder = NamedBuilder("custom context")
    with nh.run(executor), nh.scope(context_builder=builder, task_references={"injected": injected}, user_prompt_suffix_fragments=["scoped suffix"]):
        assert asyncio.run(workflow(original)) == 7
    assert agent.prompts == ["custom context\n\nconfigured suffix\n\nscoped suffix"]
    request = builder.requests[0]
    assert request.environment is request.step_context.environment
    assert request.environment.resolve("payload") is original
    assert request.environment.resolve("injected", source="task") is injected
    assert request.execution_reference == request.step_context.execution_reference
    assert request.context_limits is configuration.context_limits
    assert request.context_limits.locals_max_tokens == 17
    assert request.explicit_reference_names == ("payload",)
    assert request.explicit_reference_paths == ("payload",)
    assert request.output_binding_names == ("result",)
    assert request.binding_name_to_type == {"result": int}
    assert request.return_annotation is int
    with pytest.raises(TypeError):
        request.binding_name_to_type["result"] = str  # type: ignore[index]


def test_builder_inheritance_replacement_restoration_and_new_run_reset() -> None:
    agent = RecordingAgent()
    executor = nh.AgentStepExecutor.from_agent(agent=agent)
    parent = NamedBuilder("parent")
    child = NamedBuilder("child")
    with nh.run(executor), nh.scope(context_builder=parent):
        with nh.scope(context_builder=nh.UNSET):
            asyncio.run(workflow(None))
        with nh.scope(context_builder=child):
            asyncio.run(workflow(None))
        with nh.run(executor):
            asyncio.run(workflow(None))
        asyncio.run(workflow(None))
    assert agent.prompts[0:2] == ["parent", "child"]
    assert "<<<NH:PROGRAM>>>" in agent.prompts[2]
    assert agent.prompts[3] == "parent"
    assert len(parent.requests) == 2 and len(child.requests) == 1


def test_context_builder_preserves_native_multimodal_items() -> None:
    image = BinaryContent(data=b"image bytes", media_type="image/png")
    cache_point = CachePoint()

    class Builder:
        def build(self, request: ContextRequest) -> Sequence[UserContent]:
            return ["image:", image, cache_point, "continue"]

    agent = RecordingAgent()
    with nh.run(nh.AgentStepExecutor.from_agent(agent=agent)), nh.scope(context_builder=Builder()):
        assert asyncio.run(workflow(image)) == 7
    content = agent.prompts[0]
    assert isinstance(content, tuple)
    assert content == ("image:", image, cache_point, "continue")
    assert content[1] is image and content[2] is cache_point


def test_default_builder_can_be_composed_by_a_custom_builder() -> None:
    class Builder:
        def build(self, request: ContextRequest) -> Sequence[UserContent]:
            return [*DefaultContextBuilder().build(request), "\ncustom summary"]

    agent = RecordingAgent()
    configuration = nh.StepExecutorConfiguration(user_prompt_suffix_fragments=("single suffix",))
    with nh.run(nh.AgentStepExecutor.from_agent(agent=agent, configuration=configuration)), nh.scope(context_builder=Builder()):
        asyncio.run(workflow("input"))
    content = agent.prompts[0]
    assert isinstance(content, str) and "<<<NH:PROGRAM>>>" in content
    assert content.count("single suffix") == 1
    assert content.endswith("custom summary\n\nsingle suffix")


@pytest.mark.parametrize("invalid", [None, "builder", {}, nh.Extend([])])
def test_invalid_builder_leaves_scope_configuration_intact(invalid: object) -> None:
    agent = RecordingAgent()
    parent = NamedBuilder("parent")
    with nh.run(nh.AgentStepExecutor.from_agent(agent=agent)), nh.scope(context_builder=parent):
        reference = nh.get_execution_reference()
        with pytest.raises(TypeError), nh.scope(context_builder=invalid):  # type: ignore[arg-type]
            pass
        assert nh.get_execution_reference() is reference
        asyncio.run(workflow(None))
    assert agent.prompts == ["parent"]


@pytest.mark.parametrize("invalid_content", ["text instead of sequence", b"bytes", {"text": "invalid"}, [object()], None])
def test_invalid_builder_content_fails_before_model_execution(invalid_content: object) -> None:
    class Builder:
        def build(self, request: ContextRequest) -> Any:
            return invalid_content

    events: list[StepFinished] = []
    agent = RecordingAgent()
    with (
        nh.run(nh.AgentStepExecutor.from_agent(agent=agent)),
        nh.scope(context_builder=Builder(), lifecycle=StepLifecycle(events.append)),
        pytest.raises(nh.ExecutionError, match="ContextBuilder.build"),
    ):
        asyncio.run(workflow(None))
    assert not agent.prompts
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "executor"
    assert isinstance(events[0].original_exception, TypeError)


def test_builder_failure_has_one_terminal_record_and_no_model_request() -> None:
    failure = ValueError("context unavailable")

    class Builder:
        def build(self, request: ContextRequest) -> Sequence[UserContent]:
            raise failure

    events: list[StepFinished] = []
    agent = RecordingAgent()
    with (
        nh.run(nh.AgentStepExecutor.from_agent(agent=agent)),
        nh.scope(context_builder=Builder(), lifecycle=StepLifecycle(events.append)),
        pytest.raises(nh.ExecutionError) as caught,
    ):
        asyncio.run(workflow(None))
    assert not agent.prompts
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].original_exception is caught.value.__cause__ is failure
