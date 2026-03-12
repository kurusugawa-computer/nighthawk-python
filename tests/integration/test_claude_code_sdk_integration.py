from pathlib import Path

import nighthawk as nh
from tests.integration.skip_helpers import requires_claude_code_sdk_integration


def test_claude_code_natural_step_uses_tool(tmp_path: Path) -> None:
    requires_claude_code_sdk_integration()

    import logfire

    logfire.configure(send_to_logfire="if-token-present")
    logfire.instrument_mcp()
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModelSettings

    run_configuration = nh.StepExecutorConfiguration(
        model="claude-code-sdk:sonnet",
        model_settings=ClaudeCodeSdkModelSettings(
            working_directory=str(tmp_path.resolve()),
        ).model_dump(),
    )

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=run_configuration,
    )

    with nh.run(step_executor):

        @nh.natural_function
        def test_function() -> str:
            result = ""
            """natural
            Set <:result> to "2".
            """

            return result

        assert test_function() == "2"


def test_claude_skill() -> None:
    requires_claude_code_sdk_integration()

    import logfire

    logfire.configure(send_to_logfire="if-token-present", console=logfire.ConsoleOptions(verbose=True))
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModelSettings

    working_directory = Path(__file__).absolute().parent / "agent_working_directory"

    try:
        (working_directory / "test.txt").unlink(missing_ok=True)

        configuration = nh.StepExecutorConfiguration(
            model="claude-code-sdk:sonnet",
            model_settings=ClaudeCodeSdkModelSettings(
                permission_mode="bypassPermissions",
                setting_sources=["project"],
                claude_allowed_tool_names=("Skill", "Bash"),
                working_directory=str(working_directory.resolve()),
            ).model_dump(),
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
                Then, without changing the current working directory, return the result of the `pwd` command.
                """

            result = test_function()

            assert result == str(working_directory)
            assert (working_directory / "test.txt").is_file()
    finally:
        (working_directory / "test.txt").unlink(missing_ok=True)


def test_claude_skill_calc() -> None:
    requires_claude_code_sdk_integration()

    import logfire

    logfire.configure(send_to_logfire="if-token-present", console=logfire.ConsoleOptions(verbose=True))
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModelSettings

    working_directory = Path(__file__).absolute().parent / "agent_working_directory"

    configuration = nh.StepExecutorConfiguration(
        model="claude-code-sdk:haiku",
        model_settings=ClaudeCodeSdkModelSettings(
            permission_mode="bypassPermissions",
            setting_sources=["project"],
            claude_allowed_tool_names=("Skill", "Bash"),
            working_directory=str(working_directory.resolve()),
        ).model_dump(),
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
            """

            return result

        result = test_function()

        assert result == 1 + 2 * 8


def test_claude_mcp_callback() -> None:
    requires_claude_code_sdk_integration()

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

    from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModelSettings

    configuration = nh.StepExecutorConfiguration(
        model="claude-code-sdk:haiku",
        model_settings=ClaudeCodeSdkModelSettings(
            permission_mode="bypassPermissions",
            setting_sources=["project"],
            claude_allowed_tool_names=("Bash",),
        ).model_dump(),
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
