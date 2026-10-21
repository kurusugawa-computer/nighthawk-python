from __future__ import annotations

import asyncio
from typing import Any

import pytest

import nighthawk as nh
from nighthawk.lifecycle import StepLifecycle
from nighthawk.runtime import OperationFinished, Pass, StepCandidate, StepEnvironment, StepRequest, evaluate_expression_async, inspect_value
from tests.support.executors import StubExecutor
from tests.support.prompts import build_step_context, build_user_prompt_text, locals_section


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
        python_name_to_value={}, python_globals={}, local_name_set=frozenset({"inspect"}), task_name_to_value={"inspect": "shared"}
    )
    namespace = environment.expression_namespace()
    with pytest.raises(UnboundLocalError):
        eval("inspect", namespace, namespace)
    assert eval(f"{environment.runtime_reference}.inspect(None)", namespace, namespace)["preview"] is None
    assert eval(environment.reference("inspect", source="task"), namespace, namespace) == "shared"


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
    invalid = environment.discover(source="python", cursor=first["next_cursor"])
    assert "without a cursor" in str(invalid["recovery"])
    environment.assign("a", 1)
    environment.assign("b", 2)
    page: Any = environment.discover(source="step", max_items=1)
    environment.assign("c", 3)
    assert "listing changed" in str(environment.discover(source="step", cursor=page["next_cursor"])["error"])


def test_default_context_prioritizes_explicit_names_and_shows_task_provenance() -> None:
    context = build_step_context(python_globals={}, python_locals={"a": 1, "z": 7})
    configuration = nh.StepExecutorConfiguration(context_limits=nh.StepContextLimits(locals_max_items=1))
    prompt = build_user_prompt_text(processed_natural_program="Use <z>.", step_context=context, configuration=configuration)
    assert "z: int = 7" in locals_section(prompt)
    assert "Omitted: a" in prompt
    assert ".environment.discover()" in prompt
    context.environment = StepEnvironment(python_name_to_value=context.step_locals, python_globals={}, task_name_to_value={"shared": 5})
    prompt = build_user_prompt_text(processed_natural_program="Review state.", step_context=context)
    assert "Origin: task; name: shared" in prompt
    assert "shared: int = 5" in prompt


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
        result = asyncio.run(evaluate_expression_async(context, expression))
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

    @nh.function
    def workflow() -> None:
        findings: list[annotation_effect()] = []  # type: ignore[valid-type]  # noqa: F841 - referenced by Natural.
        """execute
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


def test_member_discovery_progresses_and_invalidated_cursor_has_recovery() -> None:
    class Surface:
        pass

    value = Surface()
    for name in ("a", "b", "c", "d", "e"):
        setattr(value, name, name)
    first = inspect_value(value, max_items=2)
    assert [item["name"] for item in first["members"]] == ["a", "b"]  # type: ignore[union-attr]
    second = inspect_value(value, max_items=2, cursor=first["next_cursor"])  # type: ignore[arg-type]
    assert [item["name"] for item in second["members"]] == ["c", "d"]  # type: ignore[union-attr]
    third = inspect_value(value, max_items=2, cursor=second["next_cursor"])  # type: ignore[arg-type]
    assert [item["name"] for item in third["members"]] == ["e"]  # type: ignore[union-attr]
    assert third["next_cursor"] is None
    value.f = "new"  # type: ignore[attr-defined]
    invalid = inspect_value(value, max_items=2, cursor=first["next_cursor"])  # type: ignore[arg-type]
    assert "listing changed" in str(invalid["error"])
    assert "without a cursor" in str(invalid["recovery"])
