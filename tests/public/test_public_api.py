from pathlib import Path

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import NighthawkError
from tests.execution.stub_executor import StubExecutor


class FakeMemory(BaseModel):
    n: int = 0


def test_environment_replace_and_getter(tmp_path: Path):
    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            workspace_root=tmp_path,
        )
    ):
        environment = nh.get_environment()
        assert environment.workspace_root == tmp_path.resolve()
        assert environment.execution_configuration == configuration.execution_configuration

    with pytest.raises(NighthawkError):
        nh.get_environment()


def test_environment_override_workspace_root_nesting(tmp_path: Path):
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    root1.mkdir()
    root2.mkdir()

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            workspace_root=root1,
        )
    ):
        assert nh.get_environment().workspace_root == root1.resolve()

        with nh.environment_override(workspace_root=root2):
            assert nh.get_environment().workspace_root == root2.resolve()

        assert nh.get_environment().workspace_root == root1.resolve()


def test_environment_override_configuration_replaces_configuration(tmp_path: Path):
    configuration_1 = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    configuration_2 = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(model="openai-responses:gpt-5-nano"),
    )

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration_1.execution_configuration,
            execution_executor=StubExecutor(),
            workspace_root=tmp_path,
        )
    ):
        assert nh.get_environment().execution_configuration == configuration_1.execution_configuration

        with nh.environment_override(
            execution_configuration=configuration_2.execution_configuration,
        ):
            assert nh.get_environment().execution_configuration == configuration_2.execution_configuration

        assert nh.get_environment().execution_configuration == configuration_1.execution_configuration


def test_environment_override_requires_existing_environment(tmp_path: Path):
    with pytest.raises(NighthawkError):
        with nh.environment_override(workspace_root=tmp_path):
            pass


def test_execution_configuration_model_default_applies():
    configuration = nh.ExecutionConfiguration()
    assert configuration.model == "openai-responses:gpt-5-nano"


def test_execution_configuration_model_requires_provider_model_format():
    with pytest.raises(ValueError, match="provider:model"):
        nh.ExecutionConfiguration(model="openai-responses")

    with pytest.raises(ValueError, match="provider:model"):
        nh.ExecutionConfiguration(model=":gpt-5-nano")

    with pytest.raises(ValueError, match="provider:model"):
        nh.ExecutionConfiguration(model="openai-responses:")

    with pytest.raises(ValueError, match="provider:model"):
        nh.ExecutionConfiguration(model="openai-responses:gpt-5-nano:extra")


def test_decorated_function_requires_environment():
    @nh.fn
    def f(x: int):
        f"""natural
        <:result>
        {{"execution_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        f(1)
