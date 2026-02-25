import os
from pathlib import Path

import pytest

import nighthawk as nh


def test_claude_code_natural_step_uses_tool(tmp_path: Path) -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

    import logfire

    logfire.configure(send_to_logfire="if-token-present")
    logfire.instrument_mcp()
    logfire.instrument_pydantic_ai()

    run_configuration = nh.RunConfiguration(model="claude-code:default")

    environment_value = nh.Environment(
        run_configuration=run_configuration,
        step_executor=nh.AgentStepExecutor(
            run_configuration=run_configuration,
            model_settings={
                "working_directory": str(tmp_path.resolve()),
            },
        ),
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

    import logfire

    logfire.configure(send_to_logfire="if-token-present", console=logfire.ConsoleOptions(verbose=True))
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.claude_code import ClaudeCodeModelSettings

    working_directory = Path(__file__).absolute().parent / "agent_working_directory"

    try:
        (working_directory / "test.txt").unlink(missing_ok=True)

        configuration = nh.RunConfiguration(model="claude-code:default")

        environment = nh.Environment(
            run_configuration=configuration,
            step_executor=nh.AgentStepExecutor(
                run_configuration=configuration,
                model_settings=ClaudeCodeModelSettings(
                    permission_mode="bypassPermissions",
                    setting_sources=["project"],
                    claude_allowed_tool_names=("Skill", "Bash"),
                    working_directory=str(working_directory.resolve()),
                ),
            ),
        )
        with nh.run(environment):

            @nh.natural_function
            def test_function():
                """natural
                ---
                deny: [pass, raise]
                ---
                Execute the `hoge` skill.
                Then, without changing the current working directory, return the result of the `bash -c pwd` command.
                """

            result = test_function()

            assert result == str(working_directory)
            assert (working_directory / "test.txt").is_file()
    finally:
        (working_directory / "test.txt").unlink(missing_ok=True)


def test_claude_skill_calc() -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

    import logfire

    logfire.configure(send_to_logfire="if-token-present", console=logfire.ConsoleOptions(verbose=True))
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.claude_code import ClaudeCodeModelSettings

    working_directory = Path(__file__).absolute().parent / "agent_working_directory"

    configuration = nh.RunConfiguration(model="claude-code:default")

    environment = nh.Environment(
        run_configuration=configuration,
        step_executor=nh.AgentStepExecutor(
            run_configuration=configuration,
            model_settings=ClaudeCodeModelSettings(
                permission_mode="bypassPermissions",
                setting_sources=["project"],
                claude_allowed_tool_names=("Skill", "Bash"),
                working_directory=str(working_directory.resolve()),
            ),
        ),
    )
    with nh.run(environment):

        @nh.natural_function
        def test_function():
            def calc(a, b):
                return a + b * 8

            """natural
            ---
            deny: [pass, raise]
            ---
            Execute the `test` skill.
            <:result>
            """

            return result

        result = test_function()

        assert result == 1 + 2 * 8


def test_claude_mcp_callback() -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

    import logfire

    logfire.configure(
        send_to_logfire="if-token-present",
        console=logfire.ConsoleOptions(
            verbose=True,
        ),
    )
    logfire.instrument_mcp()
    logfire.instrument_pydantic_ai(
        event_mode="logs",
    )

    from nighthawk.backends.claude_code import ClaudeCodeModelSettings

    configuration = nh.RunConfiguration(model="claude-code:default")

    environment = nh.Environment(
        run_configuration=configuration,
        step_executor=nh.AgentStepExecutor(
            run_configuration=configuration,
            model_settings=ClaudeCodeModelSettings(
                permission_mode="bypassPermissions",
                setting_sources=["project"],
                claude_allowed_tool_names=("Bash",),
            ),
        ),
    )
    with nh.run(environment):

        @nh.natural_function
        def test_function():
            def calc(a, b):
                return a + b * 8

            """natural
            ---
            deny: [pass, raise]
            ---
            return the result of the `calc(1,2)` function call.
            """

        assert test_function() == 17
