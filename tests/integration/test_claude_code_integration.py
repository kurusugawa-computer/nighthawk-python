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

    from nighthawk.backends.claude_code import ClaudeCodeModelSettings

    run_configuration = nh.StepExecutorConfiguration(
        model="claude-code:gpt-5.3-codex",
        model_settings=ClaudeCodeModelSettings(
            claude_max_turns=6,
            working_directory=str(tmp_path.resolve()),
        ),
    )

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=run_configuration,
    )

    with nh.run(step_executor):

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

        configuration = nh.StepExecutorConfiguration(
            model="claude-code:gpt-5.3-codex",
            model_settings=ClaudeCodeModelSettings(
                permission_mode="bypassPermissions",
                setting_sources=["project"],
                claude_allowed_tool_names=("Skill", "Bash"),
                claude_max_turns=6,
                working_directory=str(working_directory.resolve()),
            ),
        )

        step_executor = nh.AgentStepExecutor.from_configuration(
            configuration=configuration,
        )
        with nh.run(step_executor):

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

    configuration = nh.StepExecutorConfiguration(
        model="claude-code:gpt-5.3-codex",
        model_settings=ClaudeCodeModelSettings(
            permission_mode="bypassPermissions",
            setting_sources=["project"],
            claude_allowed_tool_names=("Skill", "Bash"),
            claude_max_turns=6,
            working_directory=str(working_directory.resolve()),
        ),
    )

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=configuration,
    )
    with nh.run(step_executor):

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

    configuration = nh.StepExecutorConfiguration(
        model="claude-code:gpt-5.3-codex",
        model_settings=ClaudeCodeModelSettings(
            permission_mode="bypassPermissions",
            setting_sources=["project"],
            claude_allowed_tool_names=("Bash",),
            claude_max_turns=6,
        ),
    )

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=configuration,
    )
    with nh.run(step_executor):

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


@pytest.mark.asyncio
async def test_claude_simple_call():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    if os.getenv("ANTHROPIC_BASE_URL") is None or os.getenv("ANTHROPIC_AUTH_TOKEN") is None:
        pytest.skip("Claude Code integration test requires ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN")

    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
    )
    from claude_agent_sdk.types import ResultMessage

    result_message: ResultMessage | None = None
    options = ClaudeAgentOptions(
        model="haiku",
        output_format={"type": "json_schema", "schema": {"additionalProperties": False, "properties": {"kind": {"enum": ["pass", "return", "raise"], "type": "string"}, "return_value": {"type": "string"}, "raise_message": {"type": "string"}}, "required": ["kind"], "title": "StepOutcome", "type": "object"}},
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Respond with the answer to 1 + 1.")

        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                result_message = message

    assert result_message is not None
    assert result_message.structured_output == {"kind": "return", "return_value": "2"}
