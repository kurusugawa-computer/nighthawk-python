import os
from pathlib import Path

import pytest

import nighthawk as nh


def test_claude_code_natural_step_uses_tool(tmp_path: Path) -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

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


def test_claude_skill() -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

    from nighthawk.backends.claude_code import ClaudeAgentSdkModelSettings

    workspace_root = Path(__file__).absolute().parent / "claude_working_directory"
    (workspace_root / "test.txt").unlink(missing_ok=True)

    configuration = nh.RunConfiguration(model="claude-code:default")

    environment = nh.Environment(
        run_configuration=configuration,
        step_executor=nh.AgentStepExecutor(
            run_configuration=configuration,
            model_settings=ClaudeAgentSdkModelSettings(
                permission_mode="bypassPermissions",
                setting_sources=["project"],
                claude_allowed_tool_names=("Skill", "Bash"),
            ),
        ),
        workspace_root=workspace_root,
    )
    with nh.run(environment):

        @nh.natural_function
        def test_function():
            """natural
            ---
            deny: [pass, raise]
            ---
            Execute the `hoge` skill.
            And then, return the result of the `bash -c pwd` command.
            """

        result = test_function()

        assert result == str(workspace_root)
        assert (workspace_root / "test.txt").is_file()
        (workspace_root / "test.txt").unlink(missing_ok=True)
