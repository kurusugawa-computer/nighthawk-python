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
