from pathlib import Path

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import NighthawkError


class FakeMemory(BaseModel):
    n: int = 0


class FakeMemoryV1(BaseModel):
    n: int = 1


class FakeMemoryV2(BaseModel):
    n: int = 2


def test_environment_replace_and_getter(tmp_path: Path):
    configuration = nh.Configuration(
        natural_execution_configuration=nh.NaturalExecutionConfiguration(
            model="openai:gpt-5-nano",
        ),
    )
    memory = FakeMemoryV1()

    with nh.environment(
        nh.NaturalExecutionEnvironment(
            natural_execution_configuration=configuration.natural_execution_configuration,
            natural_executor=nh.StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):
        environment = nh.get_environment()
        assert environment.workspace_root == tmp_path.resolve()
        assert environment.natural_execution_configuration == configuration.natural_execution_configuration
        assert environment.memory is not None
        assert isinstance(environment.memory, FakeMemoryV1)

    with pytest.raises(NighthawkError):
        nh.get_environment()


def test_environment_override_workspace_root_nesting(tmp_path: Path):
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    root1.mkdir()
    root2.mkdir()

    configuration = nh.Configuration(
        natural_execution_configuration=nh.NaturalExecutionConfiguration(
            model="openai:gpt-5-nano",
        ),
    )
    memory = FakeMemory()

    with nh.environment(
        nh.NaturalExecutionEnvironment(
            natural_execution_configuration=configuration.natural_execution_configuration,
            natural_executor=nh.StubExecutor(),
            memory=memory,
            workspace_root=root1,
        )
    ):
        assert nh.get_environment().workspace_root == root1.resolve()

        with nh.environment_override(workspace_root=root2):
            assert nh.get_environment().workspace_root == root2.resolve()

        assert nh.get_environment().workspace_root == root1.resolve()


def test_environment_override_configuration_replaces_memory(tmp_path: Path):
    configuration_1 = nh.Configuration(
        natural_execution_configuration=nh.NaturalExecutionConfiguration(
            model="openai:gpt-5-nano",
        ),
    )
    configuration_2 = nh.Configuration(
        natural_execution_configuration=nh.NaturalExecutionConfiguration(
            model="openai:gpt-5-nano",
        ),
    )
    memory1 = FakeMemoryV1()

    with nh.environment(
        nh.NaturalExecutionEnvironment(
            natural_execution_configuration=configuration_1.natural_execution_configuration,
            natural_executor=nh.StubExecutor(),
            memory=memory1,
            workspace_root=tmp_path,
        )
    ):
        memory_in_context = nh.get_environment().memory
        assert isinstance(memory_in_context, FakeMemoryV1)

        memory2 = FakeMemoryV2()
        with nh.environment_override(
            natural_execution_configuration=configuration_2.natural_execution_configuration,
            memory=memory2,
        ):
            memory_in_overridden_context = nh.get_environment().memory
            assert isinstance(memory_in_overridden_context, FakeMemoryV2)
            assert memory_in_overridden_context is memory2

        assert nh.get_environment().memory is memory_in_context


def test_environment_override_requires_existing_environment(tmp_path: Path):
    with pytest.raises(NighthawkError):
        with nh.environment_override(workspace_root=tmp_path):
            pass


def test_decorated_function_requires_environment():
    @nh.fn
    def f(x: int):
        """natural
        <:result>
        {{"natural_final": {{"effect": null, "error": null}}, "bindings": {{"result": {x + 1}}}}}
        """
        result = 0
        return result

    with pytest.raises(NighthawkError):
        f(1)
