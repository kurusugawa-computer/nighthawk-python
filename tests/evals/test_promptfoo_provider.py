import json

import pytest

import nighthawk as nh
from evals.promptfoo import provider
from nighthawk.runtime import assign_expression, evaluate_expression
from nighthawk.testing import CallbackExecutor, ScriptedExecutor, StepCall, StepResponse, pass_response, raise_response, return_response


def _install_executor(monkeypatch: pytest.MonkeyPatch, executor: nh.StepExecutor) -> None:
    def build_executor(*, configuration: nh.StepExecutorConfiguration) -> nh.StepExecutor:
        assert configuration.execution_model_settings == {}
        return executor

    monkeypatch.setattr(nh.AgentStepExecutor, "from_configuration", staticmethod(build_executor))


def _evaluate(**variables: str) -> dict:
    return provider.call_api(
        "",
        {"config": {"model": "test:model", "suffix_variant": "examples"}},
        {"vars": {"natural_program": "Complete this fixture.", **variables}},
    )


def test_evaluation_commits_values_preserves_effects_and_restores_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    original_builder = provider._step_executor_module.build_model_response_instructions
    original_name_to_tool = provider._declarations_module._builtin_tool_name_to_tool
    original_registration = provider._declarations_module._builtin_tools_registered

    def respond(call: StepCall) -> StepResponse:
        assert call.allowed_outcome_kinds == ("pass", "return", "raise")
        context = nh.get_step_context()
        evaluate_expression(context, "items.append(9)")
        assign_expression(context, "count", "len(items)")
        return return_response("items.clear()")

    _install_executor(monkeypatch, CallbackExecutor(respond))
    result = _evaluate(input_bindings='{"items": [1, 2]}', output_binding_names='["count"]')
    output = json.loads(result["output"])
    assert output["outcome_kind"] == "return"
    assert output["return_value"] == "items.clear()"
    assert output["bindings"] == {"count": 3}
    assert output["step_locals"]["items"] == [1, 2, 9]
    assert result["tokenUsage"] == {"prompt": 0, "completion": 0, "total": 0, "numRequests": 0}
    assert provider._step_executor_module.build_model_response_instructions is original_builder
    assert provider._declarations_module._builtin_tool_name_to_tool is original_name_to_tool
    assert provider._declarations_module._builtin_tools_registered is original_registration


def test_evaluation_reports_dsl_raise_as_an_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_executor(monkeypatch, ScriptedExecutor([raise_response("invalid input", error_type=ValueError)]))
    result = _evaluate()
    assert "error" not in result
    output = json.loads(result["output"])
    assert output["outcome_kind"] == "raise"
    assert output["raise_message"] == "invalid input"
    assert output["raise_error_type"] == "ValueError"
    assert output["bindings"] == {}


def test_evaluation_validates_outputs_and_restores_variants_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    original_builder = provider._step_executor_module.build_model_response_instructions
    original_name_to_tool = provider._declarations_module._builtin_tool_name_to_tool
    _install_executor(monkeypatch, ScriptedExecutor([pass_response(binding_name_to_value={"count": "invalid"})]))
    result = _evaluate(input_bindings='{"count": 0}', output_binding_names='["count"]')
    assert "error" in result
    output = json.loads(result["output"])
    assert output["outcome_kind"] == "error"
    assert output["error_type"] == "ExecutionError"
    assert provider._step_executor_module.build_model_response_instructions is original_builder
    assert provider._declarations_module._builtin_tool_name_to_tool is original_name_to_tool


def test_evaluation_outcome_override_preserves_frontmatter_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = ScriptedExecutor([pass_response()])
    _install_executor(monkeypatch, executor)
    result = _evaluate(natural_program="---\ndeny: [pass]\n---\nComplete the fixture.", allowed_outcome_kinds='["pass", "continue"]')
    assert executor.calls[0].allowed_outcome_kinds == ("continue",)
    assert json.loads(result["output"])["outcome_kind"] == "error"


def test_evaluation_reports_invalid_configuration() -> None:
    result = _evaluate(allowed_outcome_kinds='["unknown"]')
    assert json.loads(result["output"])["error_type"] == "ValueError"
