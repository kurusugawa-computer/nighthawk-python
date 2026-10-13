from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import tiktoken
from pydantic import BaseModel, model_serializer
from pydantic_ai import RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

from nighthawk.runtime import StepEnvironment, inspect_value
from nighthawk.runtime.step_context import ToolResultRenderingPolicy
from nighthawk.tools._tool_return_content import resolve_tool_return_segments
from nighthawk.tools.contracts import render_tool_result_json_text
from nighthawk.tools.execution import ToolResultWrapperToolset, _normalize_tool_success
from nighthawk.tools.provided import build_provided_tool_definitions
from tests.support.prompts import build_step_context


def environment_with_capabilities() -> StepEnvironment:
    return StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)})


def assert_continuation(page: Any, environment: StepEnvironment) -> None:
    namespace = environment.expression_namespace()
    references = page.get("references") or [entry["reference"] for entry in page["names"]]
    assert references
    for i, reference in enumerate(references):
        assert eval(reference, namespace, namespace) is environment.task_name_to_value[f"capability_{i:02}"]
    continued = eval(page["continuation"], namespace, namespace)
    assert continued["names"][0]["name"] == f"capability_{len(references):02}"


@pytest.mark.parametrize("container", ["dictionary", "list", "tuple", "nested"])
@pytest.mark.parametrize("style", ["default", "strict", "detailed"])
def test_composed_discovery_preserves_references_and_progress_under_budget(container: str, style: Any) -> None:
    environment = environment_with_capabilities()
    page = environment.discover(source="task")
    value: Any = (
        {"discovery": page}
        if container == "dictionary"
        else (page,)
        if container == "tuple"
        else [{"discovery": page}]
        if container == "nested"
        else [page]
    )
    normalized = _normalize_tool_success(value)["payload"]
    encoding = tiktoken.get_encoding("o200k_base")
    text = render_tool_result_json_text(value=normalized, error=None, max_tokens=128, encoding=encoding, style=style)
    observed = json.loads(text)["value"]
    assert len(encoding.encode(json.dumps(observed, separators=(",", ":")))) <= 128
    selected = observed["discovery"] if container == "dictionary" else observed[0]["discovery"] if container == "nested" else observed[0]
    assert_continuation(selected, environment)
    assert len(page["names"]) == 8  # type: ignore[arg-type]


def test_multiple_discovery_pages_share_one_response_budget() -> None:
    environment = environment_with_capabilities()
    page = environment.discover(source="task")
    value = {"first": page, "second": [page]}
    encoding = tiktoken.get_encoding("o200k_base")
    rendered = render_tool_result_json_text(value=value, error=None, max_tokens=256, encoding=encoding, style="default")
    observed = json.loads(rendered)["value"]
    assert len(encoding.encode(json.dumps(observed, separators=(",", ":")))) <= 256
    assert_continuation(observed["first"], environment)
    assert_continuation(observed["second"][0], environment)


def test_oversized_composition_returns_a_complete_recovery_action() -> None:
    environment = environment_with_capabilities()
    value = {"discovery": environment.discover(source="task"), "large": "ordinary data " * 1000}
    encoding = tiktoken.get_encoding("o200k_base")
    rendered = render_tool_result_json_text(value=value, error=None, max_tokens=64, encoding=encoding, style="default")
    observed = json.loads(rendered)["value"]
    assert "separately" in observed["recovery"]
    assert len(encoding.encode(json.dumps(observed, separators=(",", ":")))) <= 64
    assert "continuation" not in observed


def test_budget_search_serializes_ordinary_values_and_keys_once() -> None:
    serialized: list[int] = []

    class OrdinaryValue(BaseModel, frozen=True):
        number: int

        @model_serializer
        def serialize(self) -> int:
            serialized.append(self.number)
            return self.number

    environment = environment_with_capabilities()
    value = {"discovery": environment.discover(source="task"), OrdinaryValue(number=1): OrdinaryValue(number=2)}
    rendered = render_tool_result_json_text(value=value, error=None, max_tokens=128, encoding=tiktoken.get_encoding("o200k_base"), style="strict")
    observed = json.loads(rendered)["value"]
    assert serialized == [1, 2]
    assert observed["1"] == 2
    assert_continuation(observed["discovery"], environment)


def test_cycles_and_different_discovery_pages_preserve_progress() -> None:
    environment = environment_with_capabilities()

    class Service:
        pass

    for i in range(30):
        setattr(Service, f"member_{i:02}", i)
    value: list[object] = [environment.discover(source="task"), inspect_value(Service())]
    value.append(value)
    normalized = _normalize_tool_success(value)["payload"]
    encoding = tiktoken.get_encoding("o200k_base")
    rendered = render_tool_result_json_text(value=normalized, error=None, max_tokens=512, encoding=encoding, style="detailed")
    observed = json.loads(rendered)["value"]
    assert len(encoding.encode(json.dumps(observed, separators=(",", ":")))) <= 512
    assert_continuation(observed[0], environment)
    members = observed[1]["members"]
    assert members
    continued: Any = inspect_value(Service(), cursor=observed[1]["next_cursor"])
    assert continued["members"][0]["name"] == f"member_{len(members):02}"
    assert observed[2] == "<cycle>"


@pytest.mark.parametrize("budget", [1, 8, 16, 32])
def test_tiny_composed_budgets_return_complete_json(budget: int) -> None:
    value = {"discovery": environment_with_capabilities().discover(source="task")}
    encoding = tiktoken.get_encoding("o200k_base")
    rendered = render_tool_result_json_text(value=value, error=None, max_tokens=budget, encoding=encoding, style="default")
    observed = json.loads(rendered)["value"]
    assert len(encoding.encode(json.dumps(observed, separators=(",", ":")))) <= budget


def test_provider_and_content_boundary_preserve_nested_discovery_and_native_media() -> None:
    context = build_step_context(python_globals={}, python_locals={})
    context.environment = environment_with_capabilities()
    context.tool_result_rendering_policy = ToolResultRenderingPolicy("o200k_base", 512, "default")
    payload = BinaryContent(data=b"synthetic image bytes", media_type="image/png")

    async def execute() -> None:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        toolset = ToolResultWrapperToolset(FunctionToolset([definition.tool for definition in build_provided_tool_definitions()]))
        tool_name_to_tool = await toolset.get_tools(run_context)
        result: Any = await toolset.call_tool(
            "nh_eval",
            {"expression": f"{{'discovery': [{context.environment.runtime_reference}.environment.discover(source='task')]}}"},
            run_context,
            tool_name_to_tool["nh_eval"],
        )
        assert_continuation(result["discovery"][0], context.environment)
        value = [{"discovery": context.environment.discover(source="task")}, payload]
        normalized = _normalize_tool_success(value)["payload"]
        segments = resolve_tool_return_segments(
            tool_name="nh_eval", payload=normalized, rendering_policy=ToolResultRenderingPolicy("o200k_base", 512, "default")
        )
        observed = json.loads(segments.response_text)
        # Pydantic AI encodes each non-media item as a JSON string when files
        # are extracted into the trailing user content.
        assert_continuation(json.loads(observed[0])["discovery"], context.environment)
        assert any(item is payload for item in segments.ordered_user_content)

    asyncio.run(execute())
