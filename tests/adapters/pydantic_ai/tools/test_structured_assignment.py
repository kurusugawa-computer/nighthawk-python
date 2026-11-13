from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass
from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field, TypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import Tool

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.errors import ToolNameConflictError
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.adapters.pydantic_ai.tools.declarations import get_visible_tools
from nighthawk.adapters.pydantic_ai.tools.provided import get_structured_assignment_tools
from nighthawk.core.lifecycle import StepLifecycle
from nighthawk.core.operation_contract import OperationFinished, OperationRequest
from nighthawk.core.oversight import Accept, Oversight, RejectOperation
from nighthawk.core.step_context import step_context_scope
from tests.support.contexts import new_step_context
from tests.support.executors import StubExecutor
from tests.support.tools import parse_tool_error


class _Finding(BaseModel):
    severity: Literal["advisory", "blocking"]
    message: str


class _Report(BaseModel):
    subject: str
    findings: tuple[_Finding, ...] = Field(default=(), max_length=3)
    reviewed_on: datetime.date | None = None


@dataclass
class _Reading:
    unit: Literal["celsius", "fahrenheit"]
    value: float


class _Tally(BaseModel):
    name_to_count: dict[str, int]


class _Pair(BaseModel):
    endpoints: tuple[int, str]


class _Bag(BaseModel):
    items: list  # type: ignore[type-arg]


def _tool_name_list(output_name_to_contract: dict[str, object]) -> list[str]:
    step_context = new_step_context(output_binding_names=output_name_to_contract, binding_name_to_type=output_name_to_contract)
    return [tool.name for tool in get_structured_assignment_tools(step_context)]


def test_only_model_and_dataclass_outputs_receive_a_tool_in_name_order() -> None:
    assert _tool_name_list({"report": _Report, "reading": _Reading, "count": int, "names": tuple[str, ...], "choice": _Report | None}) == [
        "nh_assign_reading",
        "nh_assign_report",
    ]
    step_context = new_step_context(step_locals={"report": _Report(subject="input")}, binding_name_to_type={"report": _Report})
    assert get_structured_assignment_tools(step_context) == ()


def test_tool_arguments_are_the_contract_and_identify_the_assignment() -> None:
    step_context = new_step_context(output_binding_names={"report"}, binding_name_to_type={"report": _Report})
    (tool,) = get_structured_assignment_tools(step_context)
    definition = tool.tool_def
    assert definition.parameters_json_schema == TypeAdapter(_Report).json_schema()
    assert definition.description == "Assign the declared output report from structured arguments that follow its contract."
    assert definition.metadata == {"nighthawk.provided": True, "nighthawk.operation_kind": "assignment", "nighthawk.assignment_target": "report"}


@pytest.mark.parametrize(
    ("contract", "strict"),
    [(_Report, True), (_Reading, True), (_Tally, False), (_Pair, False), (_Bag, False)],
)
def test_strict_follows_whether_closed_objects_and_typed_arrays_express_the_contract(contract: type, strict: bool) -> None:
    step_context = new_step_context(output_binding_names={"result"}, binding_name_to_type={"result": contract})
    (tool,) = get_structured_assignment_tools(step_context)
    assert tool.tool_def.strict is strict


def test_visible_tools_place_structured_assignment_between_built_in_and_scoped_tools() -> None:
    def greet(target: str) -> str:
        return f"hello, {target}"

    step_context = new_step_context(output_binding_names={"report"}, binding_name_to_type={"report": _Report})
    with nh.run(StubExecutor()), nh.scope(tools=[greet]):
        assert [tool.name for tool in get_visible_tools()] == ["nh_assign", "nh_evaluate", "greet"]
        with step_context_scope(step_context):
            assert [tool.name for tool in get_visible_tools()] == ["nh_assign", "nh_evaluate", "nh_assign_report", "greet"]
            inner = new_step_context(output_binding_names={"count"}, binding_name_to_type={"count": int})
            with step_context_scope(inner):
                assert [tool.name for tool in get_visible_tools()] == ["nh_assign", "nh_evaluate", "greet"]


def test_scoped_tool_cannot_take_the_structured_assignment_prefix() -> None:
    def impostor() -> None:
        return None

    with (
        nh.run(StubExecutor()),
        pytest.raises(ToolNameConflictError, match="nh_assign_report"),
        nh.scope(tools=[Tool(impostor, name="nh_assign_report")]),
    ):
        pass


@nh.function
async def write_report() -> _Report:
    report: _Report
    """execute
    Set <:report>.
    """
    return report  # pyright: ignore[reportUnboundVariable]  # noqa: F821


_VALID_ARGUMENTS: dict[str, object] = {
    "subject": "billing",
    "findings": [{"severity": "blocking", "message": 'The refund path prints "ok" (twice).'}],
    "reviewed_on": "2026-11-13",
}


def _run(respond: Any, **scope_arguments: Any) -> _Report:
    executor = AgentStepExecutor(configuration=StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with nh.run(executor), nh.scope(**scope_arguments):
        return asyncio.run(write_report())


def _finish(information: Any) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])


def _tool_return_list(messages: Any) -> list[object]:
    return [part.content for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]


def test_structured_assignment_commits_the_contract_instance_through_the_operation_boundary() -> None:
    operations: list[OperationFinished] = []
    seen_tool_names: list[str] = []
    seen_strict: list[bool | None] = []
    returns: list[object] = []

    async def respond(messages: Any, information: Any) -> ModelResponse:
        if not seen_tool_names:
            seen_tool_names.extend(definition.name for definition in information.function_tools)
            seen_strict.extend(definition.strict for definition in information.function_tools if definition.name == "nh_assign_report")
            return ModelResponse(parts=[ToolCallPart("nh_assign_report", _VALID_ARGUMENTS, "assign")])
        returns.extend(_tool_return_list(messages))
        return _finish(information)

    report = _run(respond, lifecycle=StepLifecycle(on_operation_finished=operations.append))

    assert report == _Report(
        subject="billing",
        findings=(_Finding(severity="blocking", message='The refund path prints "ok" (twice).'),),
        reviewed_on=datetime.date(2026, 11, 13),
    )
    assert seen_tool_names == ["nh_assign", "nh_evaluate", "nh_assign_report"]
    assert seen_strict == [True]
    # The result names the target and omits the value that the model has just written.
    assert returns == ["{'target_path': 'report', 'retained_as': 'output'}"]
    (operation,) = operations
    assert operation.status == "succeeded" and operation.phase == "completed"
    assert operation.request.kind == "assignment" and operation.request.target_path == "report"
    assert operation.request.expression is None and operation.request.expected_type is _Report
    assert operation.request.argument_name_to_value == {"value": _VALID_ARGUMENTS}


def test_contract_failures_are_recoverable_without_a_retry_budget() -> None:
    operations: list[OperationFinished] = []
    errors: list[dict[str, Any]] = []
    invalid_argument_list: list[dict[str, object]] = [
        {"subject": "billing", "findings": [{"severity": "fatal", "message": "x"}], "reviewed_on": None},
        {"findings": [], "reviewed_on": None},
        {"subject": "billing", "findings": [{"severity": "advisory", "message": str(index)} for index in range(4)], "reviewed_on": None},
    ]

    async def respond(messages: Any, information: Any) -> ModelResponse:
        returns = _tool_return_list(messages)
        if len(returns) < len(invalid_argument_list):
            return ModelResponse(parts=[ToolCallPart("nh_assign_report", invalid_argument_list[len(returns)], f"invalid-{len(returns)}")])
        if len(returns) == len(invalid_argument_list):
            errors.extend(parse_tool_error(content) for content in returns)
            return ModelResponse(parts=[ToolCallPart("nh_assign_report", _VALID_ARGUMENTS, "valid")])
        return _finish(information)

    report = _run(respond, lifecycle=StepLifecycle(on_operation_finished=operations.append))

    assert report.subject == "billing" and len(report.findings) == 1
    assert [operation.status for operation in operations] == ["failed", "failed", "failed", "succeeded"]
    assert all(error["phase"] == "validation" and error["message"].startswith("Target 'report' failed contract") for error in errors)
    assert "severity" in errors[0]["message"] and "subject" in errors[1]["message"] and "at most 3" in errors[2]["message"]


def test_rejected_structured_assignment_leaves_the_output_unassigned() -> None:
    inspected: list[OperationRequest] = []
    denied: list[ToolReturnPart] = []

    def inspect(request: OperationRequest) -> Accept | RejectOperation:
        inspected.append(request)
        value = request.argument_name_to_value.get("value")
        if isinstance(value, dict) and value.get("subject") == "forbidden":
            return RejectOperation("The subject is not reviewable", guidance="Choose the billing subject")
        return Accept()

    async def respond(messages: Any, information: Any) -> ModelResponse:
        returns = _tool_return_list(messages)
        if not returns:
            return ModelResponse(parts=[ToolCallPart("nh_assign_report", {**_VALID_ARGUMENTS, "subject": "forbidden"}, "rejected")])
        if len(returns) == 1:
            denied.extend(part for message in messages for part in message.parts if isinstance(part, ToolReturnPart))
            return ModelResponse(parts=[ToolCallPart("nh_assign_report", _VALID_ARGUMENTS, "accepted")])
        return _finish(information)

    report = _run(respond, oversight=Oversight(inspect_operation=inspect))

    assert report.subject == "billing"
    assert [request.target_path for request in inspected] == ["report", "report"]
    assert denied[0].outcome == "denied"
    assert "The subject is not reviewable" in str(denied[0].content) and "Choose the billing subject" in str(denied[0].content)
