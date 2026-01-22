from pydantic import BaseModel

import nighthawk as nh


class Memory(BaseModel):
    pass


def test_decorator_updates_output_binding_via_docstring_natural_block(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    cfg = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.openai_client import make_agent

    agent = make_agent(cfg)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=cfg,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f(x: int):
            """natural
            <:result>
            {{"assignments": [{{"target": "<result>", "expression": "x + 1"}}]}}
            """
            result = 0
            return result

        assert f(10) == 11


def test_decorator_updates_output_binding_via_inline_natural_block(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    cfg = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    from nighthawk.openai_client import make_agent

    agent = make_agent(cfg)

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=cfg,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
            natural_backend="stub",
        )
    ):

        @nh.fn
        def f(x: int):
            result = 0
            """natural
            <:result>
            {{"assignments": [{{"target": "<result>", "expression": "x * 2"}}]}}
            """
            return result

        assert f(6) == 12


def test_decorator_uses_agent_backend_by_default(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):
            from nighthawk.openai_client import NaturalEffect, NaturalFinal
            from nighthawk.tools import assign_tool

            assert deps is not None
            assign_tool(deps, "<result>", "x + 1", type_hints={})
            return FakeRunResult(
                NaturalFinal(
                    effect=NaturalEffect(type="continue", value_json=None),
                    error=None,
                )
            )

    cfg = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = Memory()
    agent = FakeAgent()

    with nh.runtime_context(
        nh.RuntimeContext(
            configuration=cfg,
            agent=agent,
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f(x: int):
            """natural
            <:result>
            {{"assignments": [{{"target": "<result>", "expression": "x + 1"}}]}}
            """
            result = 0
            return result

        assert f(10) == 11
