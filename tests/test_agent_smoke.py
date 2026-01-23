import os

import pytest


def test_agent_import_and_construction_and_run():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    from nighthawk.agent import ToolContext, make_agent
    from nighthawk.configuration import Configuration

    configuration = Configuration(
        model="openai:gpt-5-nano",
    )
    agent = make_agent(configuration)

    tool_context = ToolContext(
        context_globals={"__builtins__": __builtins__},
        context_locals={},
        allowed_binding_targets=set(),
        memory=None,
    )

    result = agent.run_sync(
        'Return exactly this JSON object and nothing else: {"effect": {"type": "continue", "value_json": null}, "error": null}',
        deps=tool_context,
    )

    assert result.output.effect is not None
    assert result.output.effect.type in ("continue", "break", "return")
    assert result.output.error is None
