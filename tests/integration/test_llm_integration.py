import os
from pathlib import Path

import logfire
import pytest

import nighthawk as nh

logfire.configure(send_to_logfire="if-token-present")
logfire.instrument_pydantic_ai()


def _requires_openai_integration():
    if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests are disabled")
    if os.getenv("OPENAI_API_KEY") is None:
        pytest.skip("OPENAI_API_KEY is required for OpenAI integration tests")

    openai_module = pytest.importorskip("pydantic_ai.models.openai")
    return openai_module.OpenAIResponsesModelSettings


def test_simple():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    from pydantic_ai import Agent

    agent = Agent(
        "openai-responses:gpt-5-nano",
        instructions="Be concise, reply with one sentence.",
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
    )
    result = agent.run_sync('Where does "hello world" come from?')
    print(result.output)


def test_agent_import_and_construction_and_run():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    from nighthawk.execution.context import ExecutionContext
    from nighthawk.execution.contracts import EXECUTION_EFFECT_TYPES
    from tests.execution.stub_executor import StubExecutor

    environment = nh.ExecutionEnvironment(
        execution_configuration=nh.ExecutionConfiguration(),
        execution_executor=StubExecutor(),
        workspace_root=Path("."),
    )

    agent_executor = nh.AgentExecutor(
        execution_configuration=environment.execution_configuration,
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="minimal"),
    )
    agent = agent_executor.agent

    system_prompts = agent._system_prompts  # type: ignore[attr-defined]
    assert any("Do the work described in <<<NH:PROGRAM>>>." in str(p) for p in system_prompts)

    tool_context = ExecutionContext(
        execution_id="test_agent_import_and_construction_and_run",
        execution_configuration=environment.execution_configuration,
        execution_globals={"__builtins__": __builtins__},
        execution_locals={},
        binding_commit_targets=set(),
    )

    result = agent.run_sync(
        'Return exactly this JSON object and nothing else: {"effect": {"type": "continue", "source_path": null}, "error": null}',
        deps=tool_context,
    )

    assert result.output.effect is not None
    assert result.output.effect.type in EXECUTION_EFFECT_TYPES
    assert result.output.error is None


def test_natural_block_evaluate_order():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    execution_configuration = nh.ExecutionConfiguration()
    execution_executor = nh.AgentExecutor(
        execution_configuration=execution_configuration,
        model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
    )

    environment = nh.ExecutionEnvironment(
        execution_configuration=execution_configuration,
        execution_executor=execution_executor,
        workspace_root=Path("."),
    )

    with nh.environment(environment):

        @nh.fn
        def test_function() -> int:
            v = 10
            """natural
            <:v> += 5
            """
            return v

        result = test_function()
        assert result == 15


def test_condition():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    environment = nh.ExecutionEnvironment(
        execution_configuration=nh.ExecutionConfiguration(),
        execution_executor=nh.AgentExecutor(
            execution_configuration=nh.ExecutionConfiguration(),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="low"),
        ),
        workspace_root=Path("."),
    )
    with nh.environment(environment):

        @nh.fn
        def test_function(v: int) -> int:
            v += 1
            """natural
            if <v> >= 10 then return 11
            else <:v> = <v> + 5
            """
            v += 1
            return v

        assert test_function(9) == 11
        assert test_function(1) == 8


def test_readme_hybrid_nesting_normalize_then_call_python_helper():
    OpenAIResponsesModelSettings = _requires_openai_integration()

    python_average_call_argument_list: list[list[float]] = []

    def python_average(numbers: list[float]) -> float:
        python_average_call_argument_list.append(numbers)
        return sum(numbers) / len(numbers)

    environment = nh.ExecutionEnvironment(
        execution_configuration=nh.ExecutionConfiguration(),
        execution_executor=nh.AgentExecutor(
            execution_configuration=nh.ExecutionConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="high"),
        ),
        workspace_root=Path("."),
    )
    with nh.environment(environment):

        @nh.fn
        def calculate_average(numbers: list[object]) -> float:
            """natural
            Normalize <numbers> into python number list (e.g., [1, 2, ...]).
            Then compute <:result> by calling <python_average>.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert calculate_average([1, "2", "three", "cuatro", "五"]) == 3.0

    assert python_average_call_argument_list
    assert python_average_call_argument_list[-1] == [1, 2, 3, 4, 5]


def test_reasoning_memo():
    OpenAIResponsesModelSettings = _requires_openai_integration()
    logfire.info("hello")

    environment = nh.ExecutionEnvironment(
        execution_configuration=nh.ExecutionConfiguration(),
        execution_executor=nh.AgentExecutor(
            execution_configuration=nh.ExecutionConfiguration(model="openai-responses:gpt-5-mini"),
            model_settings=OpenAIResponsesModelSettings(openai_reasoning_effort="high"),
        ),
        workspace_root=Path("."),
    )
    with nh.environment(environment):
        _memo: list[str] = []

        def memo(text: str):
            _memo.append(text)

        @nh.fn
        def test_function() -> str:
            answer: str = ""
            """natural
            If a+|a|=0, try to prove that a<0.
            Record the proof steps by eval <memo> dynamically.
            Step 1: List the conditions and questions in the original proposition.
            Step 2: Merge the conditions listed in Step 1 into one. Define it as wj.
            Step 3: Let us think it step by step. Please consider all possibilities. If the intersection between wj (defined in Step 2) and the negation of the question is not empty at least in one possibility, the original proposition is false. Otherwise, the original proposition is true.
            Set answer to <:answer>.
            """
            return answer

        answer = test_function()
        logfire.info(answer.replace("{", "{{").replace("}", "}}"))
        logfire.info(str(_memo).replace("{", "{{").replace("}", "}}"))
