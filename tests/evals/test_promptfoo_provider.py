import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, SystemPromptPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from evals.promptfoo import provider
from nighthawk.runtime import assign_expression, evaluate_expression
from nighthawk.testing import CallbackExecutor, ScriptedExecutor, StepCall, StepResponse, pass_response, raise_response, return_response
from nighthawk.tools.provided import build_provided_tool_definitions

pytestmark = pytest.mark.asyncio


def _install_executor(monkeypatch: pytest.MonkeyPatch, executor: nh.StepExecutor) -> None:
    def build_executor(*, configuration: nh.StepExecutorConfiguration) -> nh.StepExecutor:
        assert configuration.execution_model_settings == {}
        return executor

    monkeypatch.setattr(nh.AgentStepExecutor, "from_configuration", staticmethod(build_executor))


async def _evaluate(**variables: str) -> dict:
    return await provider.call_api(
        "",
        {"config": {"execution_model": "test:model", "basePath": "/evaluation-assets"}},
        {"vars": {"natural_program": "Complete this fixture.", **variables}},
    )


async def test_evaluation_commits_values_and_preserves_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(call: StepCall) -> StepResponse:
        assert call.allowed_outcome_kinds == ("pass", "return", "raise")
        context = nh.get_step_context()
        evaluate_expression(context, "items.append(9)")
        assign_expression(context, "count", "len(items)")
        return return_response("items.clear()")

    _install_executor(monkeypatch, CallbackExecutor(respond))
    result = await _evaluate(input_bindings='{"items": [1, 2]}', output_binding_names='["count"]')
    output = json.loads(result["output"])
    assert output["outcome_kind"] == "return"
    assert output["return_value"] == "items.clear()"
    assert output["bindings"] == {"count": 3}
    assert output["step_locals"]["items"] == [1, 2, 9]
    assert result["tokenUsage"] == {"prompt": 0, "completion": 0, "total": 0, "numRequests": 0}


async def test_evaluation_reports_dsl_raise_as_an_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_executor(monkeypatch, ScriptedExecutor([raise_response("invalid input", error_type=ValueError)]))
    result = await _evaluate()
    assert "error" not in result
    output = json.loads(result["output"])
    assert output["outcome_kind"] == "raise"
    assert output["raise_message"] == "invalid input"
    assert output["raise_error_type"] == "ValueError"
    assert output["bindings"] == {}


async def test_evaluation_preserves_effects_when_final_validation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(call: StepCall) -> StepResponse:
        evaluate_expression(nh.get_step_context(), "items.append(9)")
        nh.get_usage_meter().record(RunUsage(input_tokens=11, output_tokens=7, requests=2))
        return pass_response(binding_name_to_value={"count": "invalid"})

    _install_executor(monkeypatch, CallbackExecutor(respond))
    result = await _evaluate(input_bindings='{"count": 0, "items": []}', output_binding_names='["count"]')
    assert "error" in result
    output = json.loads(result["output"])
    assert output["outcome_kind"] == "error"
    assert output["error_type"] == "ExecutionError"
    assert output["bindings"] == {}
    assert output["step_locals"] == {"count": 0, "items": [9]}
    assert result["tokenUsage"] == {"prompt": 11, "completion": 7, "total": 18, "numRequests": 2}


async def test_evaluation_outcome_override_preserves_frontmatter_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = ScriptedExecutor([pass_response()])
    _install_executor(monkeypatch, executor)
    result = await _evaluate(natural_program="---\ndeny: [pass]\n---\nComplete the fixture.", allowed_outcome_kinds='["pass", "continue"]')
    assert executor.calls[0].allowed_outcome_kinds == ("continue",)
    assert json.loads(result["output"])["outcome_kind"] == "error"


async def test_evaluation_reports_invalid_configuration() -> None:
    result = await _evaluate(allowed_outcome_kinds='["unknown"]')
    assert json.loads(result["output"])["error_type"] == "ValueError"


async def test_evaluation_exercises_production_tools_and_awaits_fixture_calls() -> None:
    requests = 0

    async def respond(messages, information):
        nonlocal requests
        requests += 1
        expected_name_to_description = {definition.name: definition.tool.description for definition in build_provided_tool_definitions()}
        assert {tool.name: tool.description for tool in information.function_tools} == expected_name_to_description
        assert any(
            isinstance(part, SystemPromptPart) and part.content.startswith(nh.StepPromptTemplates().system_prompt_template)
            for message in messages
            for part in message.parts
        )
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("nh_assign", {"target_path": "result", "expression": "add(first=3, second=7)"})])
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}})])

    result = await provider.call_api(
        "",
        {"config": {"execution_model": FunctionModel(respond)}},
        {
            "vars": {
                "natural_program": "Set <:result> to <add>(3, 7).",
                "input_bindings": '{"add": "__callable:async_add"}',
                "output_binding_names": '["result"]',
            }
        },
    )
    assert "error" not in result, result
    output = json.loads(result["output"])
    assert output["bindings"] == {"result": 10}
    assert output["function_calls"] == [{"name": "async_add", "arguments": {"first": 3, "second": 7}}]
    assert result["tokenUsage"]["numRequests"] == requests == 2


async def test_evaluation_propagates_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(call: StepCall) -> StepResponse:
        raise asyncio.CancelledError()

    _install_executor(monkeypatch, CallbackExecutor(respond))
    with pytest.raises(asyncio.CancelledError):
        await _evaluate()


async def test_concurrent_evaluations_keep_configuration_and_values_separate() -> None:
    ready = asyncio.Barrier(2)

    async def evaluate(value: int) -> dict:
        requests = 0
        template = f"Evaluation {value}."

        async def respond(messages, information):
            nonlocal requests
            requests += 1
            assert any(isinstance(part, SystemPromptPart) and part.content.startswith(template) for message in messages for part in message.parts)
            if requests == 1:
                await ready.wait()
                return ModelResponse(parts=[ToolCallPart("nh_assign", {"target_path": "result", "expression": str(value)})])
            return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}})])

        return await provider.call_api(
            "",
            {"config": {"execution_model": FunctionModel(respond), "prompts": {"system_prompt_template": template}}},
            {"vars": {"natural_program": "Set <:result>.", "output_binding_names": '["result"]'}},
        )

    async with asyncio.timeout(5):
        results = await asyncio.gather(evaluate(1), evaluate(2))
    assert [json.loads(result["output"])["bindings"] for result in results] == [{"result": 1}, {"result": 2}]
    assert all(result["tokenUsage"]["numRequests"] == 2 for result in results)
