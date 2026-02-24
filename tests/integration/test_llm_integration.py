import os
from pathlib import Path

import logfire
import pytest
from pydantic_ai import RunContext

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext

logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()


def _requires_openai_integration():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    openai_module = pytest.importorskip("pydantic_ai.models.openai")
    return openai_module.OpenAIResponsesModelSettings


def test_simple():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    from pydantic_ai import Agent

    agent = Agent(
        "openai-responses:gpt-5-nano",
        instructions="Be concise, reply with one sentence.",
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
    )
    result = agent.run_sync('Where does "hello world" come from?')
    print(result.output)


def test_agent_import_and_construction_and_run():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    from pydantic_ai import StructuredDict

    from nighthawk.runtime.step_contract import STEP_KINDS
    from tests.execution.stub_executor import StubExecutor

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=StubExecutor(),
    )

    agent_executor = nh.AgentStepExecutor(
        run_configuration=environment_value.run_configuration,
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
    )
    agent = agent_executor.agent

    system_prompts = agent._system_prompts  # type: ignore[attr-defined]
    assert any("Do the work described in <<<NH:PROGRAM>>>." in str(p) for p in system_prompts)

    tool_context = StepContext(
        step_id="test_agent_import_and_construction_and_run",
        run_configuration=environment_value.run_configuration,
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
    )

    result = agent.run_sync(
        'Return exactly this JSON object and nothing else: {"kind": "continue"}',
        deps=tool_context,
        output_type=StructuredDict(
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["continue"]},
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
            name="StepOutcome",
        ),
    )

    assert result.output["kind"] == "continue"
    assert result.output["kind"] in STEP_KINDS


def test_natural_block_evaluate_order():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    run_configuration = nh.RunConfiguration()
    step_executor = nh.AgentStepExecutor(
        run_configuration=run_configuration,
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
    )

    environment_value = nh.Environment(
        run_configuration=run_configuration,
        step_executor=step_executor,
    )

    with nh.run(environment_value):

        @nh.natural_function
        def test_function() -> int:
            v = 10
            """natural
            <:v> += 5
            """
            return v

        result = test_function()
        assert result == 15


def test_raise_exception():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
    )
    with nh.run(environment_value):

        @nh.natural_function
        def test_function():
            """natural
            raise a <ValueError> with message "This is a test error."
            """

        with pytest.raises(ValueError, match="This is a test error."):
            test_function()


def test_condition():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
    )
    with nh.run(environment_value):

        @nh.natural_function
        def test_function(v: int) -> int:
            v += 1
            """natural
            if <v> >= 10 then return 11
            else <:v> = <v> + 5
            """
            v += 1
            return v

        assert test_function(9) == 11


def test_multiple_blocks_one_call_scope():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
        ),
    )

    with nh.run(environment_value):

        @nh.natural_function
        def f() -> int:
            x = 1
            """natural
            <:x>
            increase x by 10
            """

            """natural
            <:x>
            increase x by 20
            """

            return x

        assert f() == 31


def test_system_prompt_suffix_fragments():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
        ),
    )

    with nh.run(environment_value):
        with nh.scope(system_prompt_suffix_fragment="Hello suffix"):

            @nh.natural_function
            def f() -> int:
                x = 1
                """natural
                <:x>
                increase x by 10
                """

                return x

            assert f() == 11


def test_user_prompt_suffix_fragments():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
        ),
    )

    with nh.run(environment_value):
        with nh.scope(user_prompt_suffix_fragment="Hello suffix"):

            @nh.natural_function
            def f() -> int:
                x = 1
                """natural
                <:x>
                increase x by 10
                """

                return x

            assert f() == 11


def test_tool_visibility_scopes():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
        ),
    )

    with nh.run(environment_value):

        @nh.tool(name="hello")
        def hello(run_context: RunContext[StepContext]) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "hello"

        with nh.scope():
            with nh.scope():

                @nh.natural_function
                def f() -> str:
                    """natural
                    Call hello.
                    """
                    return ""

                f()


def test_provided_tools_smoke():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
        ),
    )

    with nh.run(environment_value):

        @nh.tool(name="my_tool")
        def my_tool(run_context: RunContext[StepContext]) -> int:  # type: ignore[no-untyped-def]
            _ = run_context
            return 1

        @nh.natural_function
        def f() -> int:
            """natural
            <:result>
            Use nh_eval("1 + 1") then my_tool() then nh_assign("result", "my_tool() + 2")
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f() == 3


def test_session_isolation(tmp_path):
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
        ),
    )

    with nh.run(environment_value):

        @nh.tool(name="tmp_write")
        def tmp_write(run_context: RunContext[StepContext]) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            path = Path(tmp_path) / "hello.txt"
            path.write_text("hello", encoding="utf-8")
            return str(path)

        @nh.natural_function
        def f() -> str:
            """natural
            <:result>
            Use tmp_write then set result to file contents.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        result = f()
        assert "hello" in result


def test_provided_tools_do_not_leak_into_outer_environment(tmp_path):
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment_value = nh.Environment(
        run_configuration=nh.RunConfiguration(),
        step_executor=nh.AgentStepExecutor(
            run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
        ),
    )

    with nh.run(environment_value):

        @nh.tool(name="tmp_write")
        def tmp_write(run_context: RunContext[StepContext]) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            path = Path(tmp_path) / "hello.txt"
            path.write_text("hello", encoding="utf-8")
            return str(path)

        with nh.scope():

            @nh.natural_function
            def f() -> str:
                """natural
                <:result>
                Use nh_eval("1 + 1") then tmp_write then set result to file contents.
                """
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            result = f()

    assert isinstance(result, str)
