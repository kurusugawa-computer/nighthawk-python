import os

import pytest


def test_openai_client_import_and_agent_construction_and_run():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    from nighthawk.configuration import Configuration
    from nighthawk.openai_client import make_agent
    from nighthawk.tools import ToolContext

    cfg = Configuration(
        model="openai:gpt-5-nano",
    )
    agent = make_agent(cfg)

    tool_ctx = ToolContext(
        context_globals={"__builtins__": __builtins__},
        context_locals={},
        allowed_local_targets=set(),
        memory=None,
    )

    result = agent.run_sync(
        'Return exactly this JSON object and nothing else: {"effect": {"type": "continue", "value_json": null}, "error": null}',
        deps=tool_ctx,
    )

    assert result.output.effect is not None
    assert result.output.effect.type in ("continue", "break", "return")
    assert result.output.error is None
