import os
from pathlib import Path

import pytest

import nighthawk as nh


def test_claude_code_natural_step_uses_tool(tmp_path: Path, monkeypatch) -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

    # Claude Code sets CLAUDECODE for nested sessions. Clear it so this integration test can run when invoked from within Claude Code.
    monkeypatch.delenv("CLAUDECODE", raising=False)

    run_configuration = nh.RunConfiguration(model="claude-code:default")

    environment_value = nh.Environment(
        run_configuration=run_configuration,
        step_executor=nh.AgentStepExecutor(run_configuration=run_configuration),
        workspace_root=tmp_path,
    )

    with nh.run(environment_value):

        @nh.natural_function
        def test_function() -> str:
            result = ""
            """natural
            <:result>
            Use nh_eval("1 + 1") to confirm arithmetic, then call nh_assign("result", "'2'").
            """

            return result

        assert test_function() == "2"
