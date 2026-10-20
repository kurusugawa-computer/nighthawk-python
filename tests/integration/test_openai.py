import pytest
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Instrumentation

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFinished, StepLifecycle
from nighthawk.runtime import Pass, StepDefinition, execute_step, execute_step_async
from nighthawk.runtime.step_context import StepContext
from tests.integration.skip_helpers import requires_openai_integration


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_explicit_namespace_and_host_writeback(asynchronous: bool) -> None:
    requires_openai_integration()
    events: list[StepFinished] = []
    saved_name_to_value: dict[str, object] = {}
    calls: list[tuple[int, int]] = []

    def calculate(first: int, second: int) -> int:
        calls.append((first, second))
        return first + second * 8

    def finished(event: StepFinished) -> None:
        assert saved_name_to_value == {"result": 17}
        events.append(event)

    executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-5.6-luna", model_settings={"openai_reasoning_effort": "low"})
    )
    program = "---\ndeny: [return, raise]\n---\nCall <calculate> with <first> and <second>. Assign its result to <:result>."
    name_to_value = {"calculate": calculate, "first": 1, "second": 2, "result": 0, "OutputType": int}
    definition = StepDefinition(
        program,
        input_binding_names=("calculate", "first", "second"),
        output_binding_names=("result",),
        binding_name_to_type={"result": "OutputType"},
    )
    with nh.run(executor), nh.scope(lifecycle=StepLifecycle(finished)):
        if asynchronous:
            outcome = await execute_step_async(
                definition,
                name_to_value=name_to_value,
                write_binding=saved_name_to_value.__setitem__,
            )
        else:
            outcome = execute_step(
                definition,
                name_to_value=name_to_value,
                write_binding=saved_name_to_value.__setitem__,
            )
    assert outcome == Pass()
    assert (1, 2) in calls
    assert name_to_value["result"] == 0
    assert len(events) == 1 and isinstance(events[0], StepCompleted)
    assert events[0].assigned_binding_name_to_value == saved_name_to_value


@pytest.mark.asyncio
async def test_async_function_call():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            execution_model="openai-responses:gpt-5.6-luna", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="high")
        ),
    )
    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]):

        @nh.function
        async def test_function():
            async def calculate(a: int, b: int) -> int:
                return a + b * 8

            """execute
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
            execution_model="openai-responses:gpt-5.6-luna", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]):

        @nh.function
        def f() -> int:
            x = 1
            """execute
            Set <:x> to <x> + 10.
            """

            """execute
            Set <:x> to <x> + 20.
            """

            return x

        assert f() == 31


def test_provided_tools_smoke():
    openai_responses_model_settings_class = requires_openai_integration()

    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(
            execution_model="openai-responses:gpt-5.6-luna", model_settings=openai_responses_model_settings_class(openai_reasoning_effort="low")
        ),
    )

    def my_tool(run_context: RunContext[StepContext]) -> int:  # type: ignore[no-untyped-def]
        _ = run_context
        return 1

    with nh.run(step_executor), nh.scope(capabilities=[Instrumentation()]), nh.scope(tools=[my_tool]):

        @nh.function
        def f() -> int:
            """execute
            Call my_tool() and set <:result> to my_tool() + 2.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f() == 3


@pytest.mark.asyncio
async def test_embedded_selection_and_request_observation():
    from tests.support.host_controls import RecordingHandler, RequestObserver

    requires_openai_integration()
    observer = RequestObserver(threshold=100_000)
    handler = RecordingHandler()
    configuration = nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-5.6-luna", model_settings={"openai_reasoning_effort": "low"})

    @nh.function
    async def child() -> int:
        with nh.scope(execution_model="openai-responses:gpt-5.6-luna"):
            """execute
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
