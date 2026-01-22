import nighthawk as nh


def test_readme_quick_example_style(tmp_path):
    cfg = nh.Configuration()
    with nh.runtime_context(nh.RuntimeContext(configuration=cfg, workspace_root=tmp_path)):

        @nh.fn
        def calculate_average(numbers: list[int]):
            """natural
            <numbers>
            <:result>
            {{"assignments": [{{"target": "<result>", "expression": "sum(numbers) / len(numbers)"}}]}}
            """
            return result  # type: ignore  # noqa: F821

        assert calculate_average([1, 2, 3, 4]) == 2.5
