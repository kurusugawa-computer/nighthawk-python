import os

import pytest


def test_simple():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIResponsesModelSettings

    agent = Agent("openai-responses:gpt-5-nano", instructions="Be concise, reply with one sentence.", model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"))
    result = agent.run_sync('Where does "hello world" come from?')
    print(result.output)


def test_agent_import_and_construction_and_run():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    from pathlib import Path

    from pydantic import BaseModel

    from nighthawk.configuration import Configuration, ExecutionConfiguration
    from nighthawk.execution.context import ExecutionContext
    from nighthawk.execution.executors import make_agent_executor
    from nighthawk.execution.llm import EXECUTION_EFFECT_TYPES

    configuration = Configuration(
        execution_configuration=ExecutionConfiguration(),
    )

    from nighthawk.execution.environment import ExecutionEnvironment
    from tests.execution.stub_executor import StubExecutor

    class FakeMemory(BaseModel):
        pass

    environment = ExecutionEnvironment(
        execution_configuration=configuration.execution_configuration,
        execution_executor=StubExecutor(),
        memory=FakeMemory(),
        workspace_root=Path("."),
    )

    agent_executor = make_agent_executor(environment.execution_configuration)
    agent = agent_executor.agent

    system_prompts = agent._system_prompts  # type: ignore[attr-defined]
    assert any("Nighthawk Natural block" in str(p) for p in system_prompts)

    tool_context = ExecutionContext(
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

    from pathlib import Path

    import logfire
    from pydantic import BaseModel

    logfire.configure(send_to_logfire="if-token-present")
    logfire.instrument_pydantic_ai()

    import nighthawk as nh
    import nighthawk.execution.executors as execution_executors

    class FakeMemory(BaseModel):
        pass

    execution_configuration = nh.ExecutionConfiguration()
    execution_executor = execution_executors.make_agent_executor(execution_configuration)

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
            Compute <:v> plus 5 and store.
            """
            return v

        result = test_function()
        assert result == 15
