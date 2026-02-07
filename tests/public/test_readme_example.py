import nighthawk as nh
from tests.execution.stub_executor import StubExecutor


def test_readme_quick_example_style(tmp_path):
    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def calculate_average(numbers: list[int]):
            f"""natural
            <numbers>
            <:result>
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {sum(numbers) / len(numbers)}}}}}
            """
            return result  # type: ignore  # noqa: F821

        assert calculate_average([1, 2, 3, 4]) == 2.5
