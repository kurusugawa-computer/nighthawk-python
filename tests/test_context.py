from pathlib import Path

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import NighthawkError


class MemoryV1(BaseModel):
    n: int = 1


class MemoryV2(BaseModel):
    n: int = 2


def test_runtime_context_replace_and_getter(tmp_path: Path):
    cfg = nh.Configuration(memory_factory=MemoryV1)

    with nh.runtime_context(nh.RuntimeContext(configuration=cfg, workspace_root=tmp_path)):
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

    cfg = nh.Configuration()

    with nh.runtime_context(nh.RuntimeContext(configuration=cfg, workspace_root=root1)):
        assert nh.get_runtime_context().workspace_root == root1.resolve()

        with nh.runtime_context_override(workspace_root=root2):
            assert nh.get_runtime_context().workspace_root == root2.resolve()

        assert nh.get_runtime_context().workspace_root == root1.resolve()


def test_runtime_context_override_configuration_regenerates_memory(tmp_path: Path):
    cfg1 = nh.Configuration(memory_factory=MemoryV1)
    cfg2 = nh.Configuration(memory_factory=MemoryV2)

    with nh.runtime_context(nh.RuntimeContext(configuration=cfg1, workspace_root=tmp_path)):
        m1 = nh.get_runtime_context().memory
        assert isinstance(m1, MemoryV1)

        with nh.runtime_context_override(configuration=cfg2):
            m2 = nh.get_runtime_context().memory
            assert isinstance(m2, MemoryV2)
            assert m2 is not m1

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
