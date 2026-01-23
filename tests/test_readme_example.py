from pydantic import BaseModel

import nighthawk as nh


class FakeMemory(BaseModel):
    pass


def test_readme_quick_example_style(tmp_path):
    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = FakeMemory()
    from nighthawk.llm import make_agent

    agent = make_agent(configuration)

    with nh.environment(
        nh.Environment(
            configuration=configuration,
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
            {{"natural_final": {{"effect": null, "error": null}}, "bindings": {{"result": {sum(numbers) / len(numbers)}}}}}
            """
            return result  # type: ignore  # noqa: F821

        assert calculate_average([1, 2, 3, 4]) == 2.5
