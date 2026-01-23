from pathlib import Path

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import NighthawkError


class Memory(BaseModel):
    n: int = 0


class MemoryV1(BaseModel):
    n: int = 1


class MemoryV2(BaseModel):
    n: int = 2


def test_runtime_context_replace_and_getter(tmp_path: Path):
    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    memory = MemoryV1()

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):
        runtime_context = nh.get_runtime_context()
        assert runtime_context.workspace_root == tmp_path.resolve()
        assert runtime_context.configuration == configuration
        assert runtime_context.memory is not None
        assert isinstance(runtime_context.memory, MemoryV1)

    with pytest.raises(NighthawkError):
        nh.get_runtime_context()


def test_runtime_context_override_workspace_root_nesting(tmp_path: Path):
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    root1.mkdir()
    root2.mkdir()

    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    from nighthawk.agent import make_agent

    agent = make_agent(configuration)

    memory = Memory()

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration,
            agent=agent,
            memory=memory,
            workspace_root=root1,
            natural_backend="stub",
        )
    ):
        assert nh.get_runtime_context().workspace_root == root1.resolve()

        with nh.runtime_context_override(workspace_root=root2):
            assert nh.get_runtime_context().workspace_root == root2.resolve()

        assert nh.get_runtime_context().workspace_root == root1.resolve()


def test_runtime_context_override_configuration_replaces_memory(tmp_path: Path):
    configuration_1 = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    configuration_2 = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    from nighthawk.agent import make_agent

    agent = make_agent(configuration_1)

    memory1 = MemoryV1()

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=configuration_1,
            agent=agent,
            memory=memory1,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):
        memory_in_context = nh.get_runtime_context().memory
        assert isinstance(memory_in_context, MemoryV1)

        memory2 = MemoryV2()
        with nh.runtime_context_override(configuration=configuration_2, memory=memory2):
            memory_in_overridden_context = nh.get_runtime_context().memory
            assert isinstance(memory_in_overridden_context, MemoryV2)
            assert memory_in_overridden_context is memory2

        assert nh.get_runtime_context().memory is memory_in_context


def test_runtime_context_override_requires_existing_context(tmp_path: Path):
    with pytest.raises(NighthawkError):
        with nh.runtime_context_override(workspace_root=tmp_path):
            pass


def test_decorated_function_requires_runtime_context():
    @nh.fn
    def f(x: int):
        """natural
        <:result>
        {{"natural_final": {{"effect": null, "error": null}}, "outputs": {{"result": {x + 1}}}}}
        """
        result = 0
        return result

    with pytest.raises(NighthawkError):
        f(1)
