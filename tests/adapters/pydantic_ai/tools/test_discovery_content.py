from __future__ import annotations

import ast
import asyncio
import re
from typing import Any

import pytest
import tiktoken
from pydantic import BaseModel, model_serializer
from pydantic_ai import RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

from nighthawk.adapters.pydantic_ai.presentation import ToolResultRenderingPolicy, rendering_scope
from nighthawk.adapters.pydantic_ai.tools._tool_return_content import resolve_tool_return_segments
from nighthawk.adapters.pydantic_ai.tools.execution import ToolResultWrapperToolset, _normalize_tool_success
from nighthawk.adapters.pydantic_ai.tools.provided import get_provided_tools
from nighthawk.core.environment import StepEnvironment
from nighthawk.core.inspection import describe_value
from tests.support.prompts import build_step_context

_ENCODING = tiktoken.get_encoding("o200k_base")


def literal_payload(text: str) -> Any:
    """Read rendered tool text, treating omission and opaque markers as None."""
    return ast.literal_eval(re.sub(r"<[^<>']*>", "None", text))


def normalized_text(value: object, max_tokens: int) -> str:
    with rendering_scope(ToolResultRenderingPolicy("o200k_base", max_tokens, 8)):
        payload = _normalize_tool_success(value)["payload"]
    assert isinstance(payload, str)
    assert len(_ENCODING.encode(payload)) <= max_tokens
    return payload


def environment_with_capabilities() -> StepEnvironment:
    return StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)})


def assert_continuation(page: Any, environment: StepEnvironment) -> None:
    namespace = environment._expression_namespace()
    references = page.get("references") or [entry["reference"] for entry in page["names"]]
    assert references
    for i, reference in enumerate(references):
        assert eval(reference, namespace, namespace) is environment.task_name_to_value[f"capability_{i:02}"]
    continued = eval(page["continuation"], namespace, namespace)
    assert continued["names"][0]["name"] == f"capability_{len(references):02}"


@pytest.mark.parametrize("container", ["dictionary", "list", "tuple", "nested"])
def test_composed_discovery_preserves_references_and_progress_under_budget(container: str) -> None:
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
    observed = literal_payload(normalized_text(value, 128))
    selected = observed["discovery"] if container == "dictionary" else observed[0]["discovery"] if container == "nested" else observed[0]
    assert_continuation(selected, environment)
    assert len(page["names"]) == 8  # type: ignore[arg-type]


def test_multiple_discovery_pages_share_one_response_budget() -> None:
    environment = environment_with_capabilities()
    page = environment.discover(source="task")
    observed = literal_payload(normalized_text({"first": page, "second": [page]}, 256))
    assert_continuation(observed["first"], environment)
    assert_continuation(observed["second"][0], environment)


def test_oversized_composition_returns_a_complete_recovery_action() -> None:
    environment = environment_with_capabilities()
    observed = literal_payload(normalized_text({"discovery": environment.discover(source="task"), "large": "ordinary data " * 1000}, 64))
    assert "separately" in observed["recovery"]
    assert "continuation" not in observed


def test_budget_search_does_not_call_application_serializers() -> None:
    serialized: list[int] = []

    class OrdinaryValue(BaseModel, frozen=True):
        number: int

        @model_serializer
        def serialize(self) -> int:
            serialized.append(self.number)
            return self.number

    environment = environment_with_capabilities()
    text = normalized_text({"discovery": environment.discover(source="task"), OrdinaryValue(number=1): OrdinaryValue(number=2)}, 128)
    assert serialized == []
    assert "OrdinaryValue(number=1): OrdinaryValue(number=2)" in text
    assert_continuation(literal_payload(text.replace("OrdinaryValue(number=1): OrdinaryValue(number=2)", "1: 2"))["discovery"], environment)


def test_cycles_and_different_discovery_pages_preserve_progress() -> None:
    environment = environment_with_capabilities()

    class Service:
        pass

    for i in range(30):
        setattr(Service, f"member_{i:02}", i)
    value: list[object] = [environment.discover(source="task"), describe_value(Service())]
    value.append(value)
    rendered = normalized_text(value, 512)
    assert rendered.endswith(", '<cycle>']")
    observed = literal_payload(rendered)
    assert_continuation(observed[0], environment)
    members = observed[1]["members"]
    assert members
    continued: Any = describe_value(Service(), cursor=observed[1]["next_cursor"])
    assert continued["members"][0]["name"] == f"member_{len(members):02}"


@pytest.mark.parametrize("budget", [1, 8, 16, 32])
def test_tiny_composed_budgets_return_complete_python_values(budget: int) -> None:
    text = normalized_text({"discovery": environment_with_capabilities().discover(source="task")}, budget)
    assert isinstance(ast.literal_eval(text), dict)


def test_provider_and_content_boundary_preserve_nested_discovery_and_native_media() -> None:
    context = build_step_context(python_globals={}, python_locals={})
    context.environment = environment_with_capabilities()
    policy = ToolResultRenderingPolicy("o200k_base", 512, 8)
    payload = BinaryContent(data=b"synthetic image bytes", media_type="image/png")

    async def execute() -> None:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        toolset = ToolResultWrapperToolset(FunctionToolset(get_provided_tools()))
        tool_name_to_tool = await toolset.get_tools(run_context)
        result: Any = await toolset.call_tool(
            "nh_evaluate",
            {"expression": f"{{'discovery': [{context.environment.runtime_reference}.environment.discover(source='task')]}}"},
            run_context,
            tool_name_to_tool["nh_evaluate"],
        )
        assert_continuation(literal_payload(result)["discovery"][0], context.environment)
        normalized = _normalize_tool_success([{"discovery": context.environment.discover(source="task")}, payload])["payload"]
        assert isinstance(normalized, list) and normalized[1] is payload
        assert_continuation(literal_payload(normalized[0])["discovery"], context.environment)
        segments = resolve_tool_return_segments(tool_name="nh_evaluate", payload=normalized)
        assert any(item is payload for item in segments.ordered_user_content)

    with rendering_scope(policy):
        asyncio.run(execute())
