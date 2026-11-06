"""Conversation constructor and function resolution expressions through core evaluation."""

from typing import cast

import pytest

from nighthawk import StepExecutorConfiguration
from nighthawk.agent import PerimeterPolicy
from nighthawk.agent.runtime import Runner, bounded_resource_allocation
from nighthawk.agent.session import CellInput, ConversationInput, ConversationPage, ConversationResult, SessionManager
from nighthawk.configuration import StepContextLimits
from nighthawk.runtime.assignment import assign_expression_async
from nighthawk.runtime.expression import evaluate_expression_async
from nighthawk.runtime.prompt import build_user_prompt
from nighthawk.runtime.step_outcome import Pass
from nighthawk.runtime.step_request import StepCandidate, StepRequest
from nighthawk.tools._rendering import render_tool_result_json_text


@pytest.mark.parametrize("asynchronous", [False, True])
def test_core_evaluation_can_construct_requests_call_function_and_publish_typed_result(asynchronous: bool) -> None:
    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            invocation = 'resolve_session_function(FunctionSelection(binding_name="add_one"))(6)'
            await assign_expression_async(step_context, "called_value", "await " + invocation if asynchronous else invocation)
            assert await evaluate_expression_async(step_context, "called_value") == 7
            expression = 'ConversationResult(block_tuple=(UserFacingBlock(kind="answer", text=f"Result is {called_value}."),))'
            await assign_expression_async(step_context, "result", expression)
            await assign_expression_async(step_context, "incremented", "called_value")
            return StepCandidate(
                outcome=Pass(),
                binding_name_to_value={
                    "result": step_context.environment.step_name_to_value["result"],
                    "incremented": step_context.environment.step_name_to_value["incremented"],
                },
            )

    runner = Runner(Executor(), PerimeterPolicy.NONE, lambda identity: bounded_resource_allocation(), accepts_aggregate_executor_accounting=True)
    session = SessionManager(runner=runner).create_session()
    try:
        prefix = "async " if asynchronous else ""
        source = f"> _call_list = []\n{prefix}def add_one(value: int) -> int:\n    _call_list.append(value)\n    return value + 1"
        assert session.submit(CellInput(source)).wait().status == "success"
        outcome = session.submit(CellInput("Call add_one with 6 and assign its result to <:incremented>.")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["incremented"] == 7
        assert session.binding_name_to_value["_call_list"] == [6]
        assert "called_value" not in session.binding_name_to_value
        assert any(output.kind == "answer" and output.text == "Result is 7." for output in outcome.output_tuple)
    finally:
        session.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_nested_natural_step_does_not_inherit_parent_conversation_locals(asynchronous: bool) -> None:
    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            if "value" in step_context.read_binding_names:
                assert "conversation_input" not in step_context.step_locals
                assert "parent_temporary" not in step_context.step_locals
                for name in ("conversation_input", "parent_temporary"):
                    with pytest.raises(NameError):
                        step_context.environment.resolve(name)
                assert step_context.step_locals["value"] == 6
                assert step_context.step_locals["child_local"] == 7
                assert await evaluate_expression_async(step_context, "shared_global") == 8
                return StepCandidate(outcome=Pass(), binding_name_to_value={"result": "six"})
            await assign_expression_async(step_context, "parent_temporary", "42")
            assert await evaluate_expression_async(step_context, 'resolve_session_function(FunctionSelection("describe"))(6)') == "six"
            assert step_context.environment.step_name_to_value["parent_temporary"] == 42
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": ConversationResult(())})

    runner = Runner(Executor(), PerimeterPolicy.NONE, lambda identity: bounded_resource_allocation(), accepts_aggregate_executor_accounting=True)
    session = SessionManager(runner=runner).create_session()
    prefix = "async " if asynchronous else ""
    source = f'''shared_global = 8
@nh.function
{prefix}def describe(value: int) -> str:
    child_local = value + 1
    result: str = ""
    """execute
    ---
    deny: [raise, return, break, continue]
    ---
    Describe <value> and write its description to <:result>.
    """
    return result
'''
    try:
        assert session.submit(CellInput(source, mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("Call describe with 6.")).wait()
        assert outcome.status == "success", outcome.output_tuple
    finally:
        session.close()


def test_core_context_and_tool_previews_are_lossy_without_changing_python_history_page() -> None:
    configuration = StepExecutorConfiguration(
        tokenizer_encoding="o200k_base",
        context_limits=StepContextLimits(locals_max_tokens=80, value_max_tokens=8, object_field_value_max_tokens=8, tool_result_max_tokens=20),
    )

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            page = await evaluate_expression_async(step_context, "query_session_history(ConversationHistoryQuery(maximum_item_count=1))")
            assert isinstance(page, ConversationPage)
            original_records = page.record_tuple
            assert "FULL_HISTORY_PAYLOAD" in original_records[0].text
            preview = render_tool_result_json_text(
                value=page,
                error=None,
                max_tokens=20,
                encoding=configuration.resolve_token_encoding(),
                style=configuration.json_renderer_style,
            )
            assert len(preview) < len(original_records[0].text)
            assert "FULL_HISTORY_PAYLOAD" not in preview
            prompt = build_user_prompt(processed_natural_program=processed_natural_program, step_context=step_context, configuration=configuration)
            assert "FULL_HISTORY_PAYLOAD" not in "".join(item for item in prompt if isinstance(item, str))
            assert page.record_tuple is original_records
            assert page.horizon > 1 and page.next_starting_index == 1
            assert page.incomplete
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": ConversationResult(())})

    runner = Runner(Executor(), PerimeterPolicy.NONE, lambda identity: bounded_resource_allocation(), accepts_aggregate_executor_accounting=True)
    session = SessionManager(runner=runner).create_session()
    try:
        session.submit(CellInput("> # " + "FULL_HISTORY_PAYLOAD " * 250)).wait()
        outcome = session.submit(CellInput("Inspect the history page.")).wait()
        assert outcome.status == "success", outcome.output_tuple
    finally:
        session.close()


def test_approved_function_aggregates_original_large_value_despite_incomplete_preview() -> None:
    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            page = cast(ConversationInput, step_context.step_locals["conversation_input"]).namespace_page
            item = next(item for item in page.item_tuple if item.name == "samples")
            assert isinstance(item.value, (list, str))
            assert item.incomplete and len(item.value) < 200
            total = await evaluate_expression_async(step_context, 'resolve_session_function(FunctionSelection("sum_all"))()')
            assert total == sum(range(100000))
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": ConversationResult(())})

    runner = Runner(Executor(), PerimeterPolicy.NONE, lambda identity: bounded_resource_allocation(), accepts_aggregate_executor_accounting=True)
    session = SessionManager(runner=runner).create_session()
    try:
        assert session.submit(CellInput("> samples = list(range(100000))\ndef sum_all() -> int:\n    return sum(samples)")).wait().status == "success"
        outcome = session.submit(CellInput("Compute the sum over all samples using sum_all.")).wait()
        assert outcome.status == "success", outcome.output_tuple
    finally:
        session.close()
