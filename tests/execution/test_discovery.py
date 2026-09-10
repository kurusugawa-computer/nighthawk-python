from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import tiktoken
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk.lifecycle import StepLifecycle
from nighthawk.runtime import OperationFinished, Pass, StepCandidate, StepEnvironment, StepRequest, eval_expression_async, inspect_value
from nighthawk.runtime.step_context import ToolResultRenderingPolicy
from nighthawk.tools.contracts import render_tool_result_json_text
from nighthawk.tools.execution import ToolResultWrapperToolset
from nighthawk.tools.provided import build_provided_tool_definitions
from tests.execution.prompt_test_helpers import build_step_context, build_user_prompt_text, locals_section
from tests.execution.stub_executor import StubExecutor


def test_runtime_access_preserves_application_inspect_and_reserves_its_own_root() -> None:
    environment = StepEnvironment(
        python_name_to_value={"inspect": "application", "__nighthawk_runtime_0": "existing"},
        python_globals={},
        local_name_set=frozenset({"__nighthawk_runtime_1"}),
        task_name_to_value={"__nighthawk_runtime_2": "task"},
    )
    assert environment.runtime_reference == "__nighthawk_runtime_3"
    namespace = environment.expression_namespace()
    assert eval("inspect", namespace, namespace) == "application"
    result = eval(f"{environment.runtime_reference}.inspect(7)", namespace, namespace)
    assert result["preview"] == 7
    with pytest.raises(ValueError):
        environment.assign(environment.runtime_reference, 0)
    assert environment.runtime_reference not in environment.snapshot_working_state()


def test_unbound_inspect_keeps_python_meaning_with_a_usable_runtime_helper() -> None:
    environment = StepEnvironment(
        python_name_to_value={}, python_globals={}, local_name_set=frozenset({"inspect"}), parent_name_to_value={"inspect": "parent"}
    )
    namespace = environment.expression_namespace()
    with pytest.raises(UnboundLocalError):
        eval("inspect", namespace, namespace)
    assert eval(f"{environment.runtime_reference}.inspect(None)", namespace, namespace)["preview"] is None
    assert eval(environment.reference("inspect", source="parent"), namespace, namespace) == "parent"


def test_opaque_names_are_discoverable_and_source_references_resolve_live_bindings() -> None:
    original = object()
    replacement = object()
    python_globals = {"opaque": original}
    environment = StepEnvironment(python_name_to_value={}, python_globals=python_globals, task_name_to_value={"opaque": "task"})
    listing = environment.discover(source="task")
    metadata = listing["names"][0]  # type: ignore[index]
    assert metadata["origin"] == "task"
    namespace = environment.expression_namespace()
    assert eval(metadata["reference"], namespace, namespace) == "task"
    reference = environment.describe("opaque")["reference"]
    assert eval(reference, namespace, namespace) is original  # type: ignore[arg-type]
    python_globals["opaque"] = replacement
    assert eval(reference, namespace, namespace) is replacement  # type: ignore[arg-type]


def test_environment_name_pages_progress_and_recover_when_membership_changes() -> None:
    environment = StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value={f"name_{i}": object() for i in range(5)})
    first: Any = environment.discover(source="task", max_items=2)
    second: Any = environment.discover(source="task", max_items=2, cursor=first["next_cursor"])
    third: Any = environment.discover(source="task", max_items=2, cursor=second["next_cursor"])
    assert [item["name"] for page in (first, second, third) for item in page["names"]] == [f"name_{i}" for i in range(5)]
    assert third["next_cursor"] is None
    namespace = environment.expression_namespace()
    assert eval(first["continuation"], namespace, namespace) == second
    invalid = environment.discover(source="parent", cursor=first["next_cursor"])
    assert "without a cursor" in str(invalid["recovery"])
    environment.assign("a", 1)
    environment.assign("b", 2)
    page: Any = environment.discover(source="step", max_items=1)
    environment.assign("c", 3)
    assert "listing changed" in str(environment.discover(source="step", cursor=page["next_cursor"])["error"])


def test_default_context_prioritizes_explicit_names_and_shows_parent_provenance() -> None:
    context = build_step_context(python_globals={}, python_locals={"a": 1, "z": 7})
    configuration = nh.StepExecutorConfiguration(context_limits=nh.StepContextLimits(locals_max_items=1))
    prompt = build_user_prompt_text(processed_natural_program="Use <z>.", step_context=context, configuration=configuration)
    assert "z: int = 7" in locals_section(prompt)
    assert "Omitted: a" in prompt
    assert ".environment.discover()" in prompt
    context.environment = StepEnvironment(python_name_to_value=context.step_locals, python_globals={}, parent_name_to_value={"inherited": 5})
    prompt = build_user_prompt_text(processed_natural_program="Review state.", step_context=context)
    assert "Origin: parent; name: inherited" in prompt
    assert "inherited: int = 5" in prompt


def test_default_context_shows_collision_sources_before_model_lookup() -> None:
    context = build_step_context(python_globals={}, python_locals={"inspect": "application"})
    context.environment = StepEnvironment(
        python_name_to_value=context.step_locals, python_globals={}, task_name_to_value={"len": "task", "inspect": "task inspection"}
    )
    prompt = build_user_prompt_text(processed_natural_program="Use <len>.", step_context=context)
    assert "Name collision: inspect; effective origin: python" in prompt
    assert "Name collision: len; effective origin: python" in prompt
    for name in ("inspect", "len"):
        assert context.environment.reference(name, source="python") in prompt
        assert context.environment.reference(name, source="task") in prompt


def test_runtime_inspection_and_discovery_are_one_evaluation_operation() -> None:
    context = build_step_context(python_globals={}, python_locals={"value": [1, 2]})
    finished: list[OperationFinished] = []
    reference = context.environment.runtime_reference
    expression = f"({reference}.inspect(value), {reference}.environment.describe('value'))"
    with nh.run(StubExecutor()), nh.scope(lifecycle=StepLifecycle(on_operation_finished=finished.append)):
        result = asyncio.run(eval_expression_async(context, expression))
        assert result[0]["preview"] == [1, 2]  # type: ignore[index]
        assert len(finished) == 1
        assert finished[0].request.expression == expression
        inspect_value([1, 2])
        assert len(finished) == 1


def test_read_only_source_annotations_are_available_without_resolving_them() -> None:
    effects: list[str] = []
    descriptions: list[dict[str, object]] = []

    def annotation_effect() -> type:
        effects.append("annotation")
        return int

    @nh.natural_function
    def workflow() -> None:
        findings: list[annotation_effect()] = []  # type: ignore[valid-type]  # noqa: F841 - referenced by Natural.
        """natural
        Describe <findings>.
        """

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            descriptions.append(request.step_context.environment.describe("findings"))
            assert request.binding_name_to_type == {}
            return StepCandidate(outcome=Pass())

    with nh.run(Executor()):
        workflow()
    assert descriptions[0]["annotation"] == "list[annotation_effect()]"
    assert descriptions[0]["output_contract"] is None
    assert effects == []


@pytest.mark.parametrize("budget", [128, 512, 2_000])
def test_text_projection_preserves_usable_discovery_continuation_under_budget(budget: int) -> None:
    environment = StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)})
    page = environment.discover(source="task")
    encoding = tiktoken.get_encoding("o200k_base")
    rendered = render_tool_result_json_text(value=page, error=None, max_tokens=budget, encoding=encoding, style="default")
    observed = json.loads(rendered)["value"]
    assert len(encoding.encode(json.dumps(observed, separators=(",", ":")))) <= budget
    namespace = environment.expression_namespace()
    next_page = eval(observed["continuation"], namespace, namespace)
    references = observed.get("references") or [entry["reference"] for entry in observed["names"]]
    for i, reference in enumerate(references):
        assert eval(reference, namespace, namespace) is environment.task_name_to_value[f"capability_{i:02}"]
    assert next_page["names"][0]["name"] == f"capability_{len(references):02}"


def test_insufficient_discovery_budget_returns_recovery_instead_of_a_broken_cursor() -> None:
    environment = StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)})
    rendered = render_tool_result_json_text(
        value=environment.discover(source="task"), error=None, max_tokens=64, encoding=tiktoken.get_encoding("o200k_base"), style="default"
    )
    observed = json.loads(rendered)["value"]
    assert "response budget" in observed["error"]
    assert "Python slices" in observed["recovery"]
    assert "next_cursor" not in observed and "continuation" not in observed


def test_provider_evaluation_keeps_discovery_references_and_pagination() -> None:
    context = build_step_context(python_globals={}, python_locals={})
    context.environment = StepEnvironment(
        python_name_to_value={}, python_globals={}, task_name_to_value={f"capability_{i:02}": object() for i in range(30)}
    )
    context.tool_result_rendering_policy = ToolResultRenderingPolicy("o200k_base", 128, "default")

    async def execute() -> None:
        run_context = RunContext(deps=context, model=TestModel(), usage=RunUsage())
        toolset = ToolResultWrapperToolset(FunctionToolset([definition.tool for definition in build_provided_tool_definitions()]))
        tool_name_to_tool = await toolset.get_tools(run_context)
        result: Any = await toolset.call_tool(
            "nh_eval",
            {"expression": f"{context.environment.runtime_reference}.environment.discover(source='task')"},
            run_context,
            tool_name_to_tool["nh_eval"],
        )
        assert result["metadata_omitted"] is True
        namespace = context.environment.expression_namespace()
        assert eval(result["continuation"], namespace, namespace)["names"][0]["name"] == "capability_01"
        assert eval(result["references"][0], namespace, namespace) is context.environment.task_name_to_value["capability_00"]

    asyncio.run(execute())
