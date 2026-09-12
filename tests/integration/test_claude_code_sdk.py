from pathlib import Path

from pydantic_ai.capabilities import Instrumentation

import nighthawk as nh
from tests.integration.skip_helpers import requires_claude_code_sdk_integration


def test_claude_code_natural_step_uses_tool(tmp_path: Path) -> None:
    requires_claude_code_sdk_integration()

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

    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]):

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
        with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]):

            @nh.natural_function
            def test_function():
                """natural
                ---
                deny: [pass, raise]
                ---
                Invoke the `hoge` skill exactly as written. It creates `test.txt` in the current working directory.
                Then run the shell command `pwd` with the agent's shell tool (Bash), without changing the current working directory.
                Do not use `nh_eval` or Python to determine the directory; the answer must come from the shell command output.
                Return the printed path as a string with leading and trailing whitespace removed.
                """

            result = test_function()

            # The marker file is the strongest evidence that the skill ran in the configured directory.
            assert (working_directory / "test.txt").is_file(), "the hoge skill did not create test.txt in the working directory"
            assert Path(str(result).strip()).resolve() == working_directory.resolve()
    finally:
        (working_directory / "test.txt").unlink(missing_ok=True)


def test_claude_skill_calc() -> None:
    requires_claude_code_sdk_integration()

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
    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]):

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
    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]):

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
