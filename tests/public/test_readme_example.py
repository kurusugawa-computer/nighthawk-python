from pydantic import BaseModel

import nighthawk as nh


class FakeMemory(BaseModel):
    pass


def test_readme_quick_example_style(tmp_path):
    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(
            model="openai:gpt-5-nano",
        ),
    )
    memory = FakeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def calculate_average(numbers: list[int]):
            """natural
            <numbers>
            <:result>
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {sum(numbers) / len(numbers)}}}}}
            """
            return result  # type: ignore  # noqa: F821

        assert calculate_average([1, 2, 3, 4]) == 2.5
