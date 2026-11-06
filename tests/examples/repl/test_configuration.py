from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from examples.repl.session_factory import EXECUTION_MODEL_TUPLE, build_session_manager, build_source_session_manager
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.adapters.pydantic_ai.harness import model_runner
from nighthawk.adapters.pydantic_ai.model_outcome import ModelStepResponse
from nighthawk.core.step_context import StepContext
from nighthawk.harness.ledger import NaturalExecutionOutcomeEvent
from nighthawk.harness.testing import InMemoryPerimeterAuditStore
from nighthawk.session import CellInput
from tests.support.harness import ConfirmedConfinement, ScriptedTermination


@pytest.mark.parametrize("model", EXECUTION_MODEL_TUPLE)
def test_terminal_factory_honors_execution_model_and_constructs_provider_lazily(model: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from nighthawk.harness.runner import Runner

    constructions: list[str] = []
    original = model_runner

    def factory(configuration: StepExecutorConfiguration) -> AgentStepExecutor:
        assert isinstance(configuration.execution_model, str)
        constructions.append(configuration.execution_model)
        return AgentStepExecutor.from_agent(
            agent=Agent(
                TestModel(model_name=configuration.execution_model, call_tools=[], custom_output_args={"result": {"kind": "pass"}}),
                output_type=ModelStepResponse,
                deps_type=StepContext,
            )
        )

    def configured(**arguments: Any) -> Runner:
        return original(**arguments, executor_factory=factory)

    monkeypatch.setattr("examples.repl.session_factory.model_runner", configured)
    manager = build_session_manager(execution_model=model)
    session = manager.create_session()
    try:
        assert session.submit(CellInput("> value = 7")).wait().run_id_tuple == ()
        assert constructions == []
        source = '''@nh.function
def answer() -> None:
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Finish this step.
    """
'''
        assert session.submit(CellInput(source, mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("> answer()")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert constructions == [model]
        event = session._run_id_to_ledger[outcome.run_id_tuple[0]].select(NaturalExecutionOutcomeEvent)[0].event
        assert isinstance(event, NaturalExecutionOutcomeEvent) and event.response_model_name_tuple == (model,)
    finally:
        session.close()


def test_factory_rejects_unknown_models_and_configures_write_services(tmp_path: Any) -> None:
    with pytest.raises(ValueError):
        build_session_manager(execution_model="test:unknown")
    with pytest.raises(ValueError):
        build_session_manager(decision_model="test:unknown")
    manager = build_source_session_manager(
        execution_model=None,
        directory=tmp_path,
        perimeter_confinement=ConfirmedConfinement(),
        perimeter_audit_store=InMemoryPerimeterAuditStore(),
        termination_mechanism=ScriptedTermination(),
    )
    session = manager.create_session()
    try:
        source_path = tmp_path / "source.txt"
        source_path.write_text("selected source")
        session.binding_name_to_value["source_path"] = source_path
        source = 'from nighthawk.harness.context import current_capabilities\n@nh.function\ndef inspect_selected() -> str:\n    return current_capabilities()["read_file"](source_path).text\n'
        assert session.submit(CellInput(source, mode="python")).wait().status == "success"
        outcome = session.submit(CellInput("> selected = inspect_selected()")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["selected"] == "selected source"
    finally:
        session.close()
