import pytest
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Instrumentation

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from tests.integration.skip_helpers import requires_openai_integration


@pytest.mark.asyncio
async def test_async_function_call():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.6-luna", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="high")
        ),
    )
    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]):

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
            model="openai-responses:gpt-5.6-luna", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]):

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


def test_provided_tools_smoke():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.6-luna", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    def my_tool(run_context: RunContext[StepContext]) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return 1

    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]), nh.scope(tools=[my_tool]):

        @nh.natural_function
        def f() -> int:
            """natural
            Call my_tool() and set <:result> to my_tool() + 2.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f() == 3


@pytest.mark.asyncio
async def test_embedded_selection_and_request_observation():
    from examples.embedded_execution import RecordingHandler, RequestObserver

    requires_openai_integration()
    observer = RequestObserver(threshold=100_000)
    handler = RecordingHandler()
    configuration = nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna", model_settings={"openai_reasoning_effort": "low"})

    @nh.natural_function(model="openai-responses:gpt-5.6-luna")
    async def child() -> int:
        """natural
        ---
        deny: [pass, raise]
        ---
        Return the integer 17.
        """
        return 0

    executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
    with nh.function_call_scope(handler), nh.run(executor), nh.scope(capabilities=[observer]):
        assert await child() == 17
        assert nh.get_step_executor() is executor
    assert len(handler.calls) == 1
    assert observer.records
    assert all(record.call_id == handler.calls[0].call_id for record in observer.records)
    assert all(record.status == "succeeded" for record in observer.records)
    print("provider_evidence", [(record.configured_model, record.reported_model, record.tokens) for record in observer.records])
    print("settings", {"openai_reasoning_effort": "low"})
