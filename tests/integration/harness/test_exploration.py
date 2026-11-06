from dataclasses import replace

from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.harness.dispatch import execute_code_capability
from nighthawk.harness.grants import Grant
from nighthawk.parts.exploration import ExplorationResult, ExplorationTask, exploration_composition
from tests.support.harness import make_runner


def test_explore_completes_with_a_typed_result() -> None:
    executor = AgentStepExecutor(configuration=StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna"))
    result = replace(make_runner(), step_executor=executor).run(
        exploration_composition(run_entry_grant=Grant.of(execute_code_capability())),
        ExplorationTask(task="Define a Python function that returns the square of an integer, call it for 12, and summarize the result."),
    )
    assert isinstance(result, ExplorationResult)
    assert result.summary
