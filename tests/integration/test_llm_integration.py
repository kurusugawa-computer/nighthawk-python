import os
from pathlib import Path

import logfire
import pytest
from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

import nighthawk as nh
import nighthawk.execution.executors

logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()


class FakeMemory(BaseModel):
    pass


def test_simple():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIResponsesModelSettings

    agent = Agent(
        "openai-responses:gpt-5-nano",
        instructions="Be concise, reply with one sentence.",
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
    )
    result = agent.run_sync('Where does "hello world" come from?')
    print(result.output)


def test_agent_import_and_construction_and_run():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    from nighthawk.execution.context import ExecutionContext
    from nighthawk.execution.llm import EXECUTION_EFFECT_TYPES
    from tests.execution.stub_executor import StubExecutor

    environment = nh.ExecutionEnvironment(
        execution_configuration=nh.ExecutionConfiguration(),
        execution_executor=StubExecutor(),
        memory=FakeMemory(),
        workspace_root=Path("."),
    )

    agent_executor = nighthawk.execution.executors.make_agent_executor(
        environment.execution_configuration,
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
    )
    agent = agent_executor.agent

    system_prompts = agent._system_prompts  # type: ignore[attr-defined]
    assert any("Nighthawk Natural block" in str(p) for p in system_prompts)

    tool_context = ExecutionContext(
        execution_id="test_agent_import_and_construction_and_run",
        execution_configuration=environment.execution_configuration,
        execution_globals={"__builtins__": __builtins__},
        execution_locals={},
        binding_commit_targets=set(),
        memory=None,
    )

    result = agent.run_sync(
        'Return exactly this JSON object and nothing else: {"effect": {"type": "continue", "source_path": null}, "error": null}',
        deps=tool_context,
    )

    assert result.output.effect is not None
    assert result.output.effect.type in EXECUTION_EFFECT_TYPES
    assert result.output.error is None


def test_natural_block_evaluate_order():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    execution_configuration = nh.ExecutionConfiguration()
    execution_executor = nighthawk.execution.executors.make_agent_executor(
        execution_configuration,
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
    )

    environment = nh.ExecutionEnvironment(
        execution_configuration=execution_configuration,
        execution_executor=execution_executor,
        memory=FakeMemory(),
        workspace_root=Path("."),
    )

    with nh.environment(environment):

        @nh.fn
        def test_function() -> int:
            v = 10
            """natural
            <:v> += 5
            """
            return v

        result = test_function()
        assert result == 15


def test_condition():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    environment = nh.ExecutionEnvironment(
        execution_configuration=nh.ExecutionConfiguration(),
        execution_executor=nighthawk.execution.executors.make_agent_executor(
            nh.ExecutionConfiguration(),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
        memory=FakeMemory(),
        workspace_root=Path("."),
    )
    with nh.environment(environment):

        @nh.fn
        def test_function(v: int) -> int:
            v += 1
            """natural
            if <v> >= 10 then return 11
            else <:v> = <v> + 5
            """
            v += 1
            return v

        assert test_function(9) == 11
        assert test_function(1) == 8
