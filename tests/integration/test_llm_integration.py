from pathlib import Path

import logfire
import pytest
from pydantic_ai import RunContext

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from tests.integration.skip_helpers import requires_openai_integration

logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()


def test_natural_block_evaluate_order():
    openai_responses_model_settings_class = requires_openai_integration()

    run_configuration = nh.StepExecutorConfiguration(model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low"))
    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=run_configuration,
    )

    with nh.run(step_executor):

        @nh.natural_function
        def test_function() -> int:
            v = 10
            """natural
            Set <:v> to <v> + 5.
            """
            return v

        result = test_function()
        assert result == 15


def test_raise_exception():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )
    with nh.run(step_executor):

        @nh.natural_function
        def test_function():
            """natural
            raise a <ValueError> with message "This is a test error."
            """

        with pytest.raises(ValueError, match="This is a test error."):
            test_function()


def test_condition():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")),
    )
    with nh.run(step_executor):

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


@pytest.mark.asyncio
async def test_async_function_call():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )
    with nh.run(step_executor):

        @nh.natural_function
        async def test_function():
            async def calculate(a: int, b: int) -> int:
                return a + b * 8

            """natural
            ---
            deny: [pass, raise]
            ---
            return the result of the `await calculate(1,2)` function call.
            """

        assert (await test_function()) == 17


def test_multiple_blocks_one_call_scope():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="minimal")
        ),
    )

    with nh.run(step_executor):

        @nh.natural_function
        def f() -> int:
            x = 1
            """natural
            Set <:x> to <x> + 10.
            """

            """natural
            Set <:x> to <x> + 20.
            """

            return x

        assert f() == 31


def test_system_prompt_suffix_fragments():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="minimal")
        ),
    )

    with nh.run(step_executor), nh.scope(system_prompt_suffix_fragment="Hello suffix"):

        @nh.natural_function
        def f() -> int:
            x = 1
            """natural
                Set <:x> to <x> + 10.
                """

            return x

        assert f() == 11


def test_user_prompt_suffix_fragments():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="minimal")
        ),
    )

    with nh.run(step_executor), nh.scope(user_prompt_suffix_fragment="Hello suffix"):

        @nh.natural_function
        def f() -> int:
            x = 1
            """natural
                Set <:x> to <x> + 10.
                """

            return x

        assert f() == 11


def test_tool_visibility_scopes():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="minimal")
        ),
    )

    with nh.run(step_executor):

        @nh.tool(name="hello")
        def hello(run_context: RunContext[StepContext]) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "hello"

        with nh.scope(), nh.scope():

            @nh.natural_function
            def f() -> str:
                """natural
                Call hello.
                """
                return ""

            f()


def test_provided_tools_smoke():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="minimal")
        ),
    )

    with nh.run(step_executor):

        @nh.tool(name="my_tool")
        def my_tool(run_context: RunContext[StepContext]) -> int:  # type: ignore[no-untyped-def]
            _ = run_context
            return 1

        @nh.natural_function
        def f() -> int:
            """natural
            Call my_tool() and set <:result> to my_tool() + 2.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f() == 3


def test_session_isolation(tmp_path):
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="minimal")
        ),
    )

    with nh.run(step_executor):

        @nh.tool(name="tmp_write")
        def tmp_write(run_context: RunContext[StepContext]) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            path = Path(tmp_path) / "hello.txt"
            path.write_text("hello", encoding="utf-8")
            return str(path)

        @nh.natural_function
        def f() -> str:
            """natural
            Call tmp_write() to create a file. Set <:result> to the return value of tmp_write().
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        result = f()
        assert "hello" in result


def test_provided_tools_do_not_leak_into_outer_scope(tmp_path):
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5-mini", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="minimal")
        ),
    )

    with nh.run(step_executor):

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
                Call tmp_write() and set <:result> to its return value.
                """
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            result = f()

    assert isinstance(result, str)
