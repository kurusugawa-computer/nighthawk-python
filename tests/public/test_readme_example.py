from pydantic import BaseModel

import nighthawk as nh


class FakeMemory(BaseModel):
    pass


def test_readme_quick_example_style(tmp_path):
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
            workspace_root=tmp_path,
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
