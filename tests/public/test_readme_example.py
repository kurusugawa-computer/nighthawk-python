import nighthawk as nh
from tests.execution.stub_executor import StubExecutor


def test_readme_quick_example_style(tmp_path):
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

        @nh.natural_function
        def calculate_average(numbers: list[int]):
            f"""natural
            <numbers>
            <:result>
            {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {sum(numbers) / len(numbers)}}}}}
            """
            return result  # type: ignore  # noqa: F821

        assert calculate_average([1, 2, 3, 4]) == 2.5
