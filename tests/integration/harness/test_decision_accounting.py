import nighthawk as nh
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.harness.ledger import NaturalExecutionOutcomeEvent
from nighthawk.harness.run_handle import RunHandle
from tests.support.harness import make_runner
from tests.support.runs import EchoInput, EchoResult, run_body


@nh.function
def _states_arithmetic_fact(text: str) -> bool:
    states_fact: bool = False
    """decide <:states_fact>
    Is <text> a correct arithmetic statement?
    """
    return states_fact


def test_decision_is_admitted_accounted_and_recorded_with_its_block_kind() -> None:
    executor = AgentStepExecutor(configuration=StepExecutorConfiguration(decision_model="openai-decisions:gpt-6-luna"))
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=f"{_states_arithmetic_fact('2 + 2 = 4')}:{_states_arithmetic_fact('2 + 2 = 5')}")

    result = run_body(body, runner=make_runner(accepts_aggregate_executor_accounting=False), step_executor=executor, handle=handle)
    assert result.value == "True:False"
    outcome_list = [
        entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent) if isinstance(entry.event, NaturalExecutionOutcomeEvent)
    ]
    assert [(event.block_kind, event.status, event.outcome_kind) for event in outcome_list] == [("decide", "success", "pass")] * 2
    for event in outcome_list:
        assert event.executor_step_count == event.executor_usage.request_count == 1
        assert event.executor_usage.total_token_count > 0
        assert all(name is not None and "luna" in name for name in event.response_model_name_tuple)
        print(f"decision={dict(event.committed_binding_name_to_value)} models={event.response_model_name_tuple} usage={event.executor_usage}")
