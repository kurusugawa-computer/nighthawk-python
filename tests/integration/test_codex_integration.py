import os
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

import nighthawk as nh
from nighthawk.backends.codex import CodexModel
from nighthawk.runtime.step_context import StepContext


class StructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: int


def _requires_codex_integration() -> None:
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")

    # This integration test requires a real `codex` executable on PATH and valid provider credentials.
    if os.getenv("CODEX_API_KEY") is None:
        pytest.skip("Codex CLI integration test requires CODEX_API_KEY")

    # Codex CLI is probabilistic and relies on local state; allow skipping in environments where it is flaky.
    if os.getenv("NIGHTHAWK_SKIP_CODEX_INTEGRATION") == "1":
        pytest.skip("Codex integration tests are skipped")


def test_codex_natural_step_uses_tool(tmp_path: Path) -> None:
    _requires_codex_integration()

    run_configuration = nh.RunConfiguration(model="codex:default")

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


def test_codex_natural_step_uses_custom_nh_tool(tmp_path: Path) -> None:
    _requires_codex_integration()

    run_configuration = nh.RunConfiguration(model="codex:default")

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
    _requires_codex_integration()

    import logfire

    logfire.configure(send_to_logfire="if-token-present", console=logfire.ConsoleOptions(verbose=True))
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.codex import CodexModelSettings

    working_directory = Path(__file__).absolute().parent / "agent_working_directory"

    try:
        (working_directory / "test.txt").unlink(missing_ok=True)

        configuration = nh.RunConfiguration(model="codex:default")

        environment = nh.Environment(
            run_configuration=configuration,
            step_executor=nh.AgentStepExecutor(
                run_configuration=configuration,
                model_settings=CodexModelSettings(
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


def test_codex_skill_calc() -> None:
    _requires_codex_integration()

    import logfire

    logfire.configure(send_to_logfire="if-token-present", console=logfire.ConsoleOptions(verbose=True))
    logfire.instrument_pydantic_ai()

    from nighthawk.backends.codex import CodexModelSettings

    working_directory = Path(__file__).absolute().parent / "agent_working_directory"

    configuration = nh.RunConfiguration(model="codex:default")

    environment = nh.Environment(
        run_configuration=configuration,
        step_executor=nh.AgentStepExecutor(
            run_configuration=configuration,
            model_settings=CodexModelSettings(
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


def test_codex_structured_output_via_output_schema(tmp_path: Path) -> None:
    _requires_codex_integration()

    run_configuration = nh.RunConfiguration(model="codex:default")

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
        model = CodexModel()

        tool_context = StepContext(
            step_id="test_codex_structured_output_via_output_schema",
            run_configuration=run_configuration,
            step_globals={"__builtins__": __builtins__},
            step_locals={},
            binding_commit_targets=set(),
        )

        from pydantic_ai import Agent

        structured_agent = Agent(
            model=model,
            deps_type=StepContext,
            output_type=StructuredOutput,
        )

        result = structured_agent.run_sync(
            'Return exactly this JSON object and nothing else: {"answer": 2}',
            deps=tool_context,
        )

        assert result.output.answer == 2
