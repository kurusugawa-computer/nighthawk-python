import nighthawk as nh


def test_readme_quick_example_style():
    @nh.fn
    def calculate_average(numbers: list[int]):
        """natural
        <numbers>
        <:result>
        {{"assignments": [{{"target": "<result>", "expression": "sum(numbers) / len(numbers)"}}]}}
        """
        return result

    assert calculate_average([1, 2, 3, 4]) == 2.5
