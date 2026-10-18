from __future__ import annotations

from collections.abc import Callable

import nighthawk as nh
from nighthawk.testing import CallbackExecutor, ScriptedExecutor, pass_response


def helper(query: str) -> list[str]:
    """Fetch items matching the query."""
    return [f"item_{query}"]


class TestExecutorSelectionPatterns:
    def test_can_mix_fast_and_deep_executors_in_one_run(self) -> None:
        fast_executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"label": "bug"})])
        deep_executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"report": "detailed analysis"})])

        @nh.natural_function
        def classify_ticket(text: str) -> str:
            label: str = ""
            """natural
            ---
            deny: [raise, return]
            ---
            Read <text> and set <:label> to one of: bug, feature, question.
            """
            return label

        @nh.natural_function
        def write_analysis_report(ticket_text: str, product_context: str) -> str:
            report: str = ""
            """natural
            ---
            deny: [raise, return]
            ---
            Read <ticket_text> and <product_context>.
            Analyze the issue, identify likely causes, and set <:report> to a detailed analysis.
            """
            return report

        with nh.run(fast_executor):
            label = classify_ticket("tests are failing")
            with nh.scope(
                step_executor=deep_executor,
                task_references={"search_repository": helper},
            ):
                report = write_analysis_report("tests are failing", "product context")

        assert label == "bug"
        assert report == "detailed analysis"
        assert len(fast_executor.calls) == 1
        assert len(deep_executor.calls) == 1


class TestStateBoundaryPatterns:
    def test_read_binding_mutation_is_visible_through_shared_reference(self) -> None:
        def handler(call):
            carry = call.step_locals["carry"]
            assert isinstance(carry, list)
            carry.append("step_1 summary")
            return pass_response(binding_name_to_value={"result": 10})

        executor = CallbackExecutor(handler)

        @nh.natural_function
        def step_1(carry: list[str]) -> int:
            result: int = 0
            """natural
            Set <:result> to 10.
            Append a one-line summary of what you did to <carry>.
            """
            return result

        carry: list[str] = []
        with nh.run(executor):
            result = step_1(carry)

        assert result == 10
        assert carry == ["step_1 summary"]

    def test_precisely_typed_callable_parameter_remains_visible_in_locals(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"result": "item summary"})])

        @nh.natural_function
        def summarize(query: str, fetch_data: Callable[[str], list[str]]) -> str:
            result: str = ""
            """natural
            Use <fetch_data> to get data for <query> and set <:result>.
            """
            return result

        with nh.run(executor):
            output = summarize("test", helper)

        assert output == "item summary"
        call = executor.calls[0]
        assert call.step_locals["fetch_data"] is helper
        assert "fetch_data" not in call.step_globals


class TestControlFlowPatterns:
    def test_post_block_logic_pattern_allows_python_validation(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"summary": "concise summary"})])

        @nh.natural_function
        def summarize(text: str) -> str:
            summary: str = ""
            """natural
            ---
            deny: [raise, return]
            ---
            Read <text> and set <:summary> to a concise summary.
            """
            if not summary.strip():
                raise ValueError("Summary must not be empty")
            return summary

        with nh.run(executor):
            result = summarize("long text")

        assert result == "concise summary"
