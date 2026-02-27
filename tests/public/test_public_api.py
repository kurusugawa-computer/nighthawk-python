import asyncio

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import NighthawkError
from nighthawk.runtime.step_executor import AgentStepExecutor
from tests.execution.stub_executor import StubExecutor


class FakeMemory(BaseModel):
    n: int = 0


def test_step_executor_replace_and_getter():
    step_executor = StubExecutor()

    with nh.run(
        step_executor,
    ):
        step_executor_value = nh.get_step_executor()
        assert step_executor_value == step_executor

    with pytest.raises(NighthawkError):
        nh.get_step_executor()


def test_scope_configuration_replaces_executor_configuration():
    class FakeAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            raise AssertionError

    configuration_1 = nh.StepExecutorConfiguration()
    configuration_2 = nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini")

    with nh.run(
        nh.AgentStepExecutor.from_agent(
            agent=FakeAgent(),
            configuration=configuration_1,
        )
    ):
        initial_step_executor = nh.get_step_executor()
        assert isinstance(initial_step_executor, AgentStepExecutor)
        assert initial_step_executor.configuration == configuration_1

        with nh.scope(
            step_executor_configuration=configuration_2,
        ):
            scoped_step_executor = nh.get_step_executor()
            assert isinstance(scoped_step_executor, AgentStepExecutor)
            assert scoped_step_executor.configuration == configuration_2

        restored_step_executor = nh.get_step_executor()
        assert isinstance(restored_step_executor, AgentStepExecutor)
        assert restored_step_executor.configuration == configuration_1


def test_scope_keeps_run_id_and_generates_new_scope_id() -> None:
    with nh.run(
        StubExecutor(),
        run_id="run-test",
    ):
        parent_execution_context = nh.get_execution_context()
        with nh.scope():
            nested_execution_context = nh.get_execution_context()

        assert parent_execution_context.run_id == "run-test"
        assert nested_execution_context.run_id == "run-test"
        assert parent_execution_context.scope_id != nested_execution_context.scope_id


def test_scope_requires_existing_step_executor():
    with pytest.raises(NighthawkError):
        with nh.scope():
            pass


def test_run_configuration_model_default_applies():
    configuration = nh.StepExecutorConfiguration()
    assert configuration.model == "openai-responses:gpt-5-nano"


def test_run_configuration_model_requires_provider_model_format():
    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(model="openai-responses")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(model=":gpt-5-nano")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(model="openai-responses:")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(model="openai-responses:gpt-5-nano:extra")


def test_agent_step_executor_constructor_supports_standard_path_with_agent() -> None:
    class FakeAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            raise AssertionError

    step_executor = nh.AgentStepExecutor(agent=FakeAgent())
    assert step_executor.configuration.model == "openai-responses:gpt-5-nano"


def test_decorated_function_requires_step_executor():
    @nh.natural_function
    def f(x: int):
        f"""natural
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        f(1)


def test_async_decorated_function_requires_step_executor():
    @nh.natural_function
    async def f(x: int):
        f"""natural
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        asyncio.run(f(1))
