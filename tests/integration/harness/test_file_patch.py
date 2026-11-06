from pathlib import Path

import nighthawk as nh
from nighthawk.adapters.files import FileSystemWriter
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.harness.grants import Grant
from nighthawk.harness.ledger import CapabilityInvocationEvent, NaturalExecutionOutcomeEvent, PerimeterEffectCompletionEvent
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.run_contract import RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.harness.testing import InMemoryPerimeterAuditStore
from nighthawk.parts.files import (
    FilePatchPolicy,
    FilePatchRequest,  # noqa: F401 - Natural read binding.
    file_patch_capability,
)


@nh.function
def _write_and_revise(target: str) -> int:
    applied_operation_count: int = 0
    """execute
    Inspect the request schema by calling <FilePatchRequest.model_json_schema>.
    Call apply_file_patch once with a request containing two ordered operations on the file at <target>:
    add the file with text 'first', then update 'first' to 'last'.
    Assign the returned applied_operation_count to <:applied_operation_count>.
    """
    return applied_operation_count


def test_live_ordered_file_patch_records_actual_effects(tmp_path: Path) -> None:
    target = tmp_path / "result.txt"
    policy = PerimeterPolicy.permitting("destructive", "durable_write_back")
    runner = Runner(
        step_executor=AgentStepExecutor(
            StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna", execution_model_settings={"openai_reasoning_effort": "low"})
        ),
        perimeter_policy=policy,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(maximum_executor_step_count=8, maximum_total_token_count=30000),
        perimeter_audit_store=InMemoryPerimeterAuditStore(),
    )
    composition = ScaffoldComposition(
        run_contract=RunContract(input_type=str, result_type=int),
        body=_write_and_revise,
        run_entry_grant=Grant.of(file_patch_capability(policy=FilePatchPolicy(path_write_allow_list=(tmp_path,)), file_writer=FileSystemWriter())),
        composition_perimeter_policy=policy,
    )
    handle = RunHandle()
    assert runner.run(composition, str(target), handle=handle) == 2
    assert target.read_text() == "last"
    invocations = handle.ledger.select(CapabilityInvocationEvent)
    assert len(invocations) == 1
    invocation = invocations[0].event
    assert isinstance(invocation, CapabilityInvocationEvent)
    assert invocation.capability_name == "apply_file_patch" and invocation.status == "success"
    completions = handle.ledger.select(PerimeterEffectCompletionEvent)
    assert len(completions) == 1
    completion = completions[0].event
    assert isinstance(completion, PerimeterEffectCompletionEvent) and completion.status == "success"
    for entry in handle.ledger.select(NaturalExecutionOutcomeEvent):
        event = entry.event
        assert isinstance(event, NaturalExecutionOutcomeEvent)
        print(f"models={event.response_model_name_tuple} usage={event.executor_usage}")
