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
    cfg = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    from nighthawk.openai_client import make_agent

    agent = make_agent(cfg)

    memory = MemoryV1()

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=cfg,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
        )
    ):
        ctx = nh.get_runtime_context()
        assert ctx.workspace_root == tmp_path.resolve()
        assert ctx.configuration == cfg
        assert ctx.memory is not None
        assert isinstance(ctx.memory, MemoryV1)

    with pytest.raises(NighthawkError):
        nh.get_runtime_context()


def test_runtime_context_override_workspace_root_nesting(tmp_path: Path):
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    root1.mkdir()
    root2.mkdir()

    cfg = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    from nighthawk.openai_client import make_agent

    agent = make_agent(cfg)

    memory = Memory()

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=cfg,
            agent=agent,
            memory=memory,
            workspace_root=root1,
        )
    ):
        assert nh.get_runtime_context().workspace_root == root1.resolve()

        with nh.runtime_context_override(workspace_root=root2):
            assert nh.get_runtime_context().workspace_root == root2.resolve()

        assert nh.get_runtime_context().workspace_root == root1.resolve()


def test_runtime_context_override_configuration_replaces_memory(tmp_path: Path):
    cfg1 = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    cfg2 = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    from nighthawk.openai_client import make_agent

    agent = make_agent(cfg1)

    memory1 = MemoryV1()

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=cfg1,
            agent=agent,
            memory=memory1,
            workspace_root=tmp_path,
        )
    ):
        m1 = nh.get_runtime_context().memory
        assert isinstance(m1, MemoryV1)

        memory2 = MemoryV2()
        with nh.runtime_context_override(configuration=cfg2, memory=memory2):
            m2 = nh.get_runtime_context().memory
            assert isinstance(m2, MemoryV2)
            assert m2 is memory2

        assert nh.get_runtime_context().memory is m1


def test_runtime_context_override_requires_existing_context(tmp_path: Path):
    with pytest.raises(NighthawkError):
        with nh.runtime_context_override(workspace_root=tmp_path):
            pass


def test_decorated_function_requires_runtime_context():
    @nh.fn
    def f(x: int):
        """natural
        <:result>
        {{"assignments": [{{"target": "<result>", "expression": "x + 1"}}]}}
        """
        result = 0
        return result

    with pytest.raises(NighthawkError):
        f(1)
