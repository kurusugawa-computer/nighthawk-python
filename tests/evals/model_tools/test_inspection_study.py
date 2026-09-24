from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from evals.model_tools.inspection_study import STUDY_CASES, build_inspection_tool, make_trial, run_trial, score_trial
from nighthawk.runtime.step_context import ToolResultRenderingPolicy
from nighthawk.tools.execution import ToolResultWrapperToolset
from nighthawk.tools.provided import build_provided_tool_definitions
from tests.support.executors import StubExecutor


def scripted_calls(case_name: str, root: str) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def assign(target: str, expression: str) -> None:
        calls.append(("nh_assign", {"target_path": target, "expression": expression}))

    def evaluate(expression: str) -> None:
        calls.append(("nh_eval", {"expression": expression}))

    final: dict[str, Any] = {"kind": "pass"}
    match case_name:
        case "assignment":
            assign("result", "search_documents(question)")
        case "opaque":
            assign("ticket", "fetch_ticket()")
            assign("result", "consume_ticket(ticket)")
        case "large":
            assign("numbers", "fetch_numbers()")
            assign("result", "sum(numbers[-100:])")
        case "mutation":
            evaluate("items.__setitem__(slice(None), [value for value in items if value >= 0])")
            assign("result", "len(items)")
        case "collision" | "helper_collision":
            assign("result", f"{root}.environment.resolve('search_documents', source='task')(question)")
        case "unbound":
            evaluate("i")
            assign("result", "'unbound'")
        case "nested":
            assign("result", "[]")
            for i in range(1, 11):
                assign("i", str(i))
                evaluate("result.append(await block_two())")
        case "discovery":
            assign("result", f"{root}.environment.resolve('z_verify', source='task')()")
        case "members" | "unbound_helper":
            assign("result", "next(name for name, value in vars(record).items() if value == 'treasure')")
        case "validation":
            assign("result", "'invalid'")
            assign("result", "'12'")
        case "continue":
            evaluate("items.sort()")
            final = {"kind": "continue"}
        case "return":
            final = {"kind": "return", "return_expression": "issue_receipt()"}
        case "approval":
            assign("result", "await approved_action()")
        case "rejection":
            evaluate("await rejected_action()")
        case "approval_partial":
            evaluate("await approved_action()")
            evaluate("await rejected_action()")
        case "partial_effect":
            assign("result", "append_event()")
            assign("result", "len(events)")
        case _:
            raise AssertionError(case_name)
    return calls, final


@pytest.mark.parametrize("case", STUDY_CASES, ids=lambda case: case.name)
@pytest.mark.parametrize("surface", ["dedicated", "helper"])
def test_scripted_semantic_success_uses_production_operations(case: Any, surface: Any) -> None:
    root = make_trial(case).context.environment.runtime_reference
    calls, final = scripted_calls(case.name, root)
    next_call = 0

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal next_call
        if next_call < len(calls):
            name, arguments = calls[next_call]
            next_call += 1
            return ModelResponse(parts=[ToolCallPart(name, arguments, str(next_call))])
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": final}, "finish")])

    result = asyncio.run(run_trial(case, FunctionModel(respond), surface, True, "short", 0, 0))
    assert result["passed"], result
    assert result["requests"]
    assert result["tool_schemas"]
    assert result["system_instructions"]


@pytest.mark.parametrize("inspection", ["names", "value"])
def test_dedicated_and_helper_discovery_return_identical_bounded_content(inspection: str) -> None:
    case = next(case for case in STUDY_CASES if case.name == ("discovery" if inspection == "names" else "members"))
    state = make_trial(case)
    state.context.tool_result_rendering_policy = ToolResultRenderingPolicy("o200k_base", 512, "strict")

    async def execute() -> None:
        run_context = RunContext(deps=state.context, model=TestModel(), usage=RunUsage())
        toolset = ToolResultWrapperToolset(
            FunctionToolset([*[definition.tool for definition in build_provided_tool_definitions()], build_inspection_tool()])
        )
        tool_name_to_tool = await toolset.get_tools(run_context)
        root = state.context.environment.runtime_reference
        arguments = {"source": "task"} if inspection == "names" else {"expression": "record"}
        expression = f"{root}.environment.discover(source='task')" if inspection == "names" else f"{root}.inspect(record)"
        with nh.run(StubExecutor()):
            dedicated = await toolset.call_tool("nh_inspect", arguments, run_context, tool_name_to_tool["nh_inspect"])
            helper = await toolset.call_tool("nh_eval", {"expression": expression}, run_context, tool_name_to_tool["nh_eval"])
        assert dedicated == helper

    asyncio.run(execute())


@pytest.mark.parametrize("case", STUDY_CASES, ids=lambda case: case.name)
def test_semantic_oracles_reject_skipped_work_even_with_expected_answer(case: Any) -> None:
    state = make_trial(case)
    failures = score_trial(state, case.outcome, case.expected)
    # A value-only task may permit an equivalent expression, but effectful,
    # required-failure, and parent-state cases must prove their behavior.
    if case.name not in ("members", "unbound_helper"):
        assert failures, case.name


def test_collision_treatment_changes_only_its_initial_presentation_section() -> None:
    case = next(case for case in STUDY_CASES if case.name == "collision")

    async def respond(messages: Any, information: Any) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])

    control = asyncio.run(run_trial(case, FunctionModel(respond), "dedicated", False, "short", 0, 0))
    treatment = asyncio.run(run_trial(case, FunctionModel(respond), "dedicated", True, "short", 0, 0))
    control_lines = control["prompts"][0].splitlines()
    treatment_lines = treatment["prompts"][0].splitlines()
    assert control_lines[:-1] == treatment_lines[:-1]
    control_value = json.loads(control_lines[-1])
    treatment_value = json.loads(treatment_lines[-1])
    collisions = treatment_value.pop("collisions")
    assert collisions and collisions[0]["name"] == "search_documents"
    assert any("source='task'" in reference for reference in collisions[0]["source_references"])
    assert control_value == treatment_value
    assert control["tool_schemas"] == treatment["tool_schemas"]
    assert control["system_instructions"] == treatment["system_instructions"]
