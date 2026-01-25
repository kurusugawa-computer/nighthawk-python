import os

import pytest


def test_agent_import_and_construction_and_run():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    from pathlib import Path

    from pydantic import BaseModel

    from nighthawk.configuration import Configuration, NaturalExecutionConfiguration
    from nighthawk.execution.context import ExecutionContext
    from nighthawk.execution.llm import make_agent

    configuration = Configuration(
        natural_execution_configuration=NaturalExecutionConfiguration(
            model="openai:gpt-5-nano",
        ),
    )

    from nighthawk.execution.environment import NaturalExecutionEnvironment
    from nighthawk.execution.executors import StubExecutor

    class FakeMemory(BaseModel):
        pass

    environment = NaturalExecutionEnvironment(
        natural_execution_configuration=configuration.natural_execution_configuration,
        natural_executor=StubExecutor(),
        memory=FakeMemory(),
        workspace_root=Path("."),
    )

    agent = make_agent(environment)

    system_prompts = agent._system_prompts  # type: ignore[attr-defined]
    assert any("Nighthawk Natural block" in str(p) for p in system_prompts)

    tool_context = ExecutionContext(
        globals={"__builtins__": __builtins__},
        locals={},
        binding_commit_targets=set(),
        memory=None,
    )

    result = agent.run_sync(
        'Return exactly this JSON object and nothing else: {"effect": {"type": "continue", "value_json": null}, "error": null}',
        deps=tool_context,
    )

    assert result.output.effect is not None
    assert result.output.effect.type in ("continue", "break", "return")
    assert result.output.error is None
