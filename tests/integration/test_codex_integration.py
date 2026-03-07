from pathlib import Path

import nighthawk as nh
from tests.integration.skip_helpers import requires_codex_integration


def test_codex_natural_step_uses_tool(tmp_path: Path) -> None:
    requires_codex_integration()

    run_configuration = nh.StepExecutorConfiguration(
        model="codex:gpt-5-mini",
        model_settings={
            "working_directory": str(tmp_path.resolve()),
        },
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


def test_codex_natural_step_uses_custom_nh_tool(tmp_path: Path) -> None:
    requires_codex_integration()

    run_configuration = nh.StepExecutorConfiguration(
        model="codex:gpt-5-mini",
        model_settings={
            "working_directory": str(tmp_path.resolve()),
        },
    )

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=run_configuration,
    )

    with nh.run(step_executor):

        @nh.tool(name="test_operation")
        def test_operation(run_context, *, a: int, b: int) -> int:  # type: ignore[no-untyped-def]
            _ = run_context
            return a + b

        @nh.natural_function
        def test_function() -> int:
            result = 0
            """natural
            Compute <:result> with test_operation(a=20, b=22).
            """
            return int(result)

        assert test_function() == 42


def test_codex_skill() -> None:
    requires_codex_integration()

    import logfire

    logfire.configure(send_to_logfire="if-token-present", console=logfire.ConsoleOptions(verbose=True))
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.codex import CodexModelSettings

    working_directory = Path(__file__).absolute().parent / "agent_working_directory"

    try:
        (working_directory / "test.txt").unlink(missing_ok=True)

        configuration = nh.StepExecutorConfiguration(
            model="codex:default",
            model_settings=CodexModelSettings(
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


def test_codex_skill_calc() -> None:
    requires_codex_integration()

    import logfire

    logfire.configure(send_to_logfire="if-token-present", console=logfire.ConsoleOptions(verbose=True))
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.codex import CodexModelSettings

    working_directory = Path(__file__).absolute().parent / "agent_working_directory"

    configuration = nh.StepExecutorConfiguration(
        model="codex:default",
        model_settings=CodexModelSettings(
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
            """

            return result

        result = test_function()

        assert result == 1 + 2 * 8
