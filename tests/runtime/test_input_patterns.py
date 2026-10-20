from collections.abc import Mapping
from types import CellType

import pytest
from pydantic import ValidationError

import nighthawk as nh
from nighthawk.runtime import StepEnvironment
from tests.support.executors import StubExecutor
from tests.support.host_controls import RecordingHandler


class FalseyMapping(dict[str, object]):
    def __bool__(self) -> bool:
        return False


class FakeAgent:
    def run_sync(self, *arguments, **keywords):
        raise AssertionError("No agent execution expected")


@pytest.mark.parametrize("invalid", [None, {}, {"model": "test:model"}, False, nh.Merge({})])
def test_executor_rejects_invalid_configuration_before_creating_agent(invalid: object, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(configuration):
        pytest.fail("Invalid configuration constructed an agent")

    monkeypatch.setattr("nighthawk.runtime.step_executor._new_agent_step_executor", unexpected)
    with pytest.raises(TypeError, match="configuration"):
        nh.AgentStepExecutor(configuration=invalid)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="configuration"):
        nh.AgentStepExecutor.from_agent(agent=FakeAgent(), configuration=invalid)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="configuration"):
        nh.AgentStepExecutor.from_configuration(configuration=invalid)  # type: ignore[arg-type]


def test_executor_defaults_and_falsey_configuration_preserve_explicit_selection() -> None:
    class FalseyConfiguration(nh.StepExecutorConfiguration):
        def __bool__(self) -> bool:
            return False

    first = nh.AgentStepExecutor.from_agent(agent=FakeAgent())
    second = nh.AgentStepExecutor.from_agent(agent=FakeAgent(), configuration=nh.UNSET)
    assert first.configuration == second.configuration
    assert first.configuration is not second.configuration
    configuration = FalseyConfiguration(model_settings={"temperature": 0})
    assert nh.AgentStepExecutor.from_agent(agent=FakeAgent(), configuration=configuration).configuration is configuration
    with pytest.raises(TypeError):
        nh.AgentStepExecutor.from_configuration(configuration=nh.UNSET)  # type: ignore[arg-type]


def test_configuration_contains_concrete_settings_and_preserves_empty_and_none_values() -> None:
    configuration = nh.StepExecutorConfiguration()
    assert configuration.model_settings == {}
    assert nh.StepExecutorConfiguration(model_settings={}).model_settings == {}
    assert nh.StepExecutorConfiguration(model_settings={"stop": None}).model_settings == {"stop": None}
    for invalid in (None, nh.UNSET):
        with pytest.raises(ValidationError):
            nh.StepExecutorConfiguration(model_settings=invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [None, "", {}, False])
def test_run_identifier_rejection_preserves_parent(invalid: object) -> None:
    with nh.run(StubExecutor(), run_id="parent"):
        reference = nh.get_execution_reference()
        with pytest.raises((TypeError, ValueError)), nh.run(StubExecutor(), run_id=invalid):  # type: ignore[arg-type]
            pytest.fail("Invalid run identifier installed context")
        assert nh.get_execution_reference() is reference


def test_run_omission_creates_identity_and_meter_while_scope_inherits_meter() -> None:
    with nh.run(StubExecutor()):
        reference = nh.get_execution_reference()
        meter = nh.get_usage_meter()
        with nh.scope(usage_meter=nh.UNSET):
            assert nh.get_usage_meter() is meter
        with nh.run(StubExecutor(), run_id=nh.UNSET, usage_meter=nh.UNSET):
            assert nh.get_execution_reference().run_id != reference.run_id
            assert nh.get_usage_meter() is not meter
        assert nh.get_usage_meter() is meter


def test_invalid_scope_input_does_not_call_model_resolver() -> None:
    resolutions = []

    class Executor:
        def run_step(self, *, request):
            raise AssertionError("No step expected")

        def resolve_model_selection(self, selection):
            resolutions.append(selection)
            return self

    executor = Executor()
    with nh.run(executor):
        reference = nh.get_execution_reference()
        with pytest.raises(TypeError), nh.scope(execution_model="test:model", task_references=None):  # type: ignore[arg-type]
            pytest.fail("Invalid scope entered")
        assert nh.get_execution_reference() is reference
        assert nh.get_step_executor() is executor
    assert resolutions == []


def test_function_handler_inherits_replaces_clears_and_validates_before_entry() -> None:
    @nh.function
    def function() -> int:
        return 1

    first, second = RecordingHandler(), RecordingHandler()
    with nh.function_call_scope(first):
        with nh.function_call_scope():
            assert function() == 1
        with nh.function_call_scope(nh.UNSET):
            assert function() == 1
        with nh.function_call_scope(None):
            assert function() == 1
        with nh.function_call_scope(second):
            assert function() == 1
        for invalid in ({}, {"invoke": function}, object()):
            with pytest.raises(TypeError), nh.function_call_scope(invalid):  # type: ignore[arg-type]
                pytest.fail("Invalid handler entered context")
        assert function() == 1
    assert len(first.calls) == 3
    assert len(second.calls) == 1


@pytest.mark.parametrize("field", ["python_name_to_cell", "task_name_to_value", "binding_name_to_annotation", "binding_name_to_type"])
def test_environment_optional_mappings_reject_none_and_invalid_keys(field: str) -> None:
    for invalid in (None, nh.UNSET, [], {1: object()}):
        with pytest.raises(TypeError, match=field):
            StepEnvironment(python_name_to_value={}, python_globals={}, **{field: invalid})  # type: ignore[arg-type]


def test_falsey_mappings_keep_live_values_and_explicit_none() -> None:
    value = []
    cell = CellType(value)
    environment = StepEnvironment(
        python_name_to_value=FalseyMapping({"local": value}),
        python_globals={},
        python_name_to_cell=FalseyMapping({"cell": cell}),  # type: ignore[arg-type]
        task_name_to_value=FalseyMapping({"task": value, "none": None}),
        binding_name_to_annotation=FalseyMapping({"local": list}),
        binding_name_to_type=FalseyMapping({"local": list}),
    )
    assert environment.resolve("local") is value
    assert environment.resolve("cell") is value
    assert environment.resolve("task") is value
    assert environment.resolve("none") is None
    assert environment.binding_name_to_type == {"local": list}
    assert environment.expression_namespace(FalseyMapping({"local": None}))["local"] is None
    assert isinstance(environment.task_name_to_value, Mapping)
