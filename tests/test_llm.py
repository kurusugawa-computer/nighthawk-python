import os

import pytest


def test_agent_import_and_construction_and_run():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    from nighthawk.context import ExecutionContext
    from nighthawk.core import Configuration
    from nighthawk.llm import make_agent

    configuration = Configuration(
        model="openai:gpt-5-nano",
    )
    agent = make_agent(configuration)

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
