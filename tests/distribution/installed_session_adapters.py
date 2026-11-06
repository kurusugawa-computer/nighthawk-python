"""Executed only from an installed-wheel environment outside the checkout."""

from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from nighthawk.adapters.files import FileSystemWriter
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.adapters.pydantic_ai.harness import model_runner
from nighthawk.adapters.pydantic_ai.model_outcome import ModelStepResponse
from nighthawk.core.step_context import StepContext
from nighthawk.core.step_request import StepCandidate
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.confinement import ConfinementConfirmation
from nighthawk.harness.grants import Grant
from nighthawk.harness.ledger import NaturalExecutionOutcomeEvent
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.testing import InMemoryPerimeterAuditStore
from nighthawk.parts.conversation import ConversationResult, DataExport, SourceExport, SourceInvocation, SourceRetention
from nighthawk.parts.files import FilePatchPolicy, file_patch_capability
from nighthawk.parts.sources import SourceCandidate
from nighthawk.session import CellInput, SessionManager


def provider(configuration: StepExecutorConfiguration) -> AgentStepExecutor:
    assert isinstance(configuration.execution_model, str)
    return AgentStepExecutor.from_agent(
        agent=Agent(
            TestModel(model_name=configuration.execution_model, call_tools=[], custom_output_args={"result": {"kind": "pass"}}),
            output_type=ModelStepResponse,
            deps_type=StepContext,
        )
    )


class ConfirmingDouble:
    def confirm(self, request: Any) -> ConfinementConfirmation:
        return ConfinementConfirmation(confinement_identity="installed-test-double")


runner = model_runner(
    model_configuration_tuple=tuple(StepExecutorConfiguration(execution_model=name) for name in ("test:A", "test:B")),
    default_execution_model="test:A",
    perimeter_policy=PerimeterPolicy.permitting_all(),
    resource_allocation_factory=lambda identity: bounded_resource_allocation(maximum_executor_step_count=24),
    executor_factory=provider,
)
session = SessionManager(runner=runner).create_session()
source = '''@nh.function
def plain() -> None:
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Finish this step.
    """

@nh.function
def child() -> None:
    with nh.scope(execution_model="test:B"):
        plain()

@nh.function
def parent() -> None:
    plain()
    child()
    plain()
'''
assert session.submit(CellInput(source, mode="python")).wait().status == "success"
outcome = session.submit(CellInput("> parent()")).wait()
assert outcome.status == "success", outcome.output_tuple
models = [
    name
    for entry in session._run_id_to_ledger[outcome.run_id_tuple[0]].select(NaturalExecutionOutcomeEvent)
    if isinstance(entry.event, NaturalExecutionOutcomeEvent)
    for name in entry.event.response_model_name_tuple
]
assert models == ["test:A", "test:B", "test:A"]
assert session.close()

proposal = CodeProposal(source="def add(value: int) -> int:\n    return value + 1\n", export_name_tuple=("add",))
responses = [
    ConversationResult((), {}, source_retention_tuple=(SourceRetention("candidate", proposal),)),
    SourceInvocation("candidate", "add", (6,)),
    ConversationResult((), {"total": 7}),
    SourceExport("candidate", str(Path.cwd() / "selected.py")),
    ConversationResult(()),
    DataExport("total", str(Path.cwd() / "selected.json")),
    ConversationResult(()),
]
audit = InMemoryPerimeterAuditStore()
runner = replace(
    runner,
    step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": result}) for result in responses]),
    perimeter_confinement=ConfirmingDouble(),
    perimeter_audit_store=audit,
    accepts_aggregate_executor_accounting=True,
)
grant = Grant((file_patch_capability(policy=FilePatchPolicy(path_write_allow_list=(Path.cwd(),)), file_writer=FileSystemWriter()),))
session = SessionManager(runner=runner, run_entry_grant=grant, composition_perimeter_policy=runner.perimeter_policy).create_session()
run_ids: list[str] = []
for task in ("retain <:candidate>", "reuse and write <:total>", "export source", "export data"):
    outcome = session.submit(CellInput(task)).wait()
    assert outcome.status == "success" and not any(output.kind == "failure" for output in outcome.output_tuple), outcome.output_tuple
    run_ids.extend(outcome.run_id_tuple)
assert len(set(run_ids)) == 4
assert isinstance(session.binding_name_to_value["candidate"], SourceCandidate)
assert session.binding_name_to_value["total"] == 7
assert (Path.cwd() / "selected.py").read_text() == proposal.source
assert (Path.cwd() / "selected.json").read_text().strip() == "7"
assert len(audit.records) == 4
assert session.close()
print("installed model selection, candidate reuse, and explicit export passed")
