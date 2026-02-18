from pathlib import Path

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import NighthawkError
from tests.execution.stub_executor import StubExecutor


class FakeMemory(BaseModel):
    n: int = 0


def test_environment_replace_and_getter(tmp_path: Path):
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
            workspace_root=tmp_path,
        )
    ):
        environment_value = nh.get_environment()
        assert environment_value.workspace_root == tmp_path.resolve()
        assert environment_value.run_configuration == configuration.run_configuration

    with pytest.raises(NighthawkError):
        nh.get_environment()


def test_scope_workspace_root_nesting(tmp_path: Path):
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    root1.mkdir()
    root2.mkdir()

    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
            workspace_root=root1,
        )
    ):
        assert nh.get_environment().workspace_root == root1.resolve()

        with nh.scope(workspace_root=root2):
            assert nh.get_environment().workspace_root == root2.resolve()

        assert nh.get_environment().workspace_root == root1.resolve()


def test_scope_configuration_replaces_configuration(tmp_path: Path):
    configuration_1 = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    configuration_2 = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(model="openai-responses:gpt-5-nano"),
    )

    with nh.run(
        nh.Environment(
            run_configuration=configuration_1.run_configuration,
            step_executor=StubExecutor(),
            workspace_root=tmp_path,
        )
    ):
        assert nh.get_environment().run_configuration == configuration_1.run_configuration

        with nh.scope(
            run_configuration=configuration_2.run_configuration,
        ):
            assert nh.get_environment().run_configuration == configuration_2.run_configuration

        assert nh.get_environment().run_configuration == configuration_1.run_configuration


def test_scope_requires_existing_environment(tmp_path: Path):
    with pytest.raises(NighthawkError):
        with nh.scope(workspace_root=tmp_path):
            pass


def test_run_configuration_model_default_applies():
    configuration = nh.RunConfiguration()
    assert configuration.model == "openai-responses:gpt-5-nano"


def test_run_configuration_model_requires_provider_model_format():
    with pytest.raises(ValueError, match="provider:model"):
        nh.RunConfiguration(model="openai-responses")

    with pytest.raises(ValueError, match="provider:model"):
        nh.RunConfiguration(model=":gpt-5-nano")

    with pytest.raises(ValueError, match="provider:model"):
        nh.RunConfiguration(model="openai-responses:")

    with pytest.raises(ValueError, match="provider:model"):
        nh.RunConfiguration(model="openai-responses:gpt-5-nano:extra")


def test_decorated_function_requires_environment():
    @nh.natural_function
    def f(x: int):
        f"""natural
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        f(1)
