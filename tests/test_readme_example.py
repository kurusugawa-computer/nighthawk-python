from pydantic import BaseModel

import nighthawk as nh


class Memory(BaseModel):
    pass


def test_readme_quick_example_style(tmp_path):
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
        def calculate_average(numbers: list[int]):
            """natural
            <numbers>
            <:result>
            {{"assignments": [{{"target": "<result>", "expression": "sum(numbers) / len(numbers)"}}]}}
            """
            return result  # type: ignore  # noqa: F821

        assert calculate_average([1, 2, 3, 4]) == 2.5
