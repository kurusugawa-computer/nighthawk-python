from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import pytest
from pydantic import Field

import nighthawk as nh
from nighthawk.errors import ExecutionError
from nighthawk.lifecycle import StepFailed, StepFinished, StepLifecycle
from nighthawk.runtime import StepDefinition
from nighthawk.runtime.step_outcome import StepOutcome
from nighthawk.testing import ScriptedExecutor
from tests.support.executors import StubExecutor


@pytest.mark.parametrize("name", ["len", "injected", "caller_value"])
@pytest.mark.asyncio
async def test_missing_reads_do_not_fall_back(execute: Callable[..., Awaitable[StepOutcome]], name: str) -> None:
    caller_value = 99
    executor = ScriptedExecutor([])
    events: list[StepFinished] = []
    with (
        nh.run(executor),
        nh.scope(task_references={"injected": 42}, lifecycle=StepLifecycle(events.append)),
        pytest.raises(nh.ExecutionError) as caught,
    ):
        await execute(StepDefinition("Read required input.", input_binding_names=(name,)), name_to_value={}, write_binding=lambda name, value: None)
    assert caller_value == 99
    assert executor.calls == []
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"
    assert isinstance(events[0].original_exception, NameError)
    assert events[0].original_exception.name == name
    assert caught.value.__cause__ is events[0].original_exception


@pytest.mark.asyncio
async def test_type_contracts_do_not_use_caller_types(execute: Callable[..., Awaitable[StepOutcome]]) -> None:
    CallerOnlyType = int  # noqa: N806
    executor = ScriptedExecutor([])
    with nh.run(executor), pytest.raises(nh.ExecutionError, match="CallerOnlyType"):
        await execute(
            StepDefinition("write output", output_binding_names=("result",), binding_name_to_type={"result": "CallerOnlyType"}),
            name_to_value={},
            write_binding=lambda name, value: None,
        )
    assert CallerOnlyType is int
    assert executor.calls == []


_global_contract_result: Annotated[int, Field(ge=0, le=10)] = 0


def test_global_output_uses_module_annotation_and_writes_back() -> None:
    @nh.function
    def workflow() -> int:
        global _global_contract_result
        """execute
        <:_global_contract_result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"_global_contract_result": "7"}}
        """
        return _global_contract_result

    global _global_contract_result
    original_value = _global_contract_result
    try:
        with nh.run(StubExecutor()):
            assert workflow() == 7
        assert _global_contract_result == 7
    finally:
        _global_contract_result = original_value


def test_nonlocal_output_infers_live_value_without_outer_annotation() -> None:
    result: object = "initial"

    @nh.function
    def workflow() -> object:
        nonlocal result
        """execute
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": "7"}}
        """
        return result

    result = 1
    with nh.run(StubExecutor()):
        assert workflow() == 7
    assert result == 7


def test_return_annotation_resolution_fails_before_executor() -> None:
    @nh.function
    def workflow() -> UnavailableReturnContract:  # type: ignore[name-defined]  # noqa: F821
        """execute
        This executor would fail if reached.
        """

    events: list[StepFinished] = []
    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=events.append)),
        pytest.raises(nh.ExecutionError, match="UnavailableReturnContract"),
    ):
        workflow()
    assert len(events) == 1
    assert isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"


def test_explicit_object_and_any_do_not_infer_initial_type() -> None:
    @nh.function
    def workflow() -> tuple[object, Any]:
        first: object = "initial"
        second: Any = "initial"
        """execute
        <:first> <:second>
        {"step_outcome": {"kind": "pass"}, "bindings": {"first": 7, "second": [1]}}
        """
        return first, second

    with nh.run(StubExecutor()):
        assert workflow() == (7, [1])


def test_compound_annotation_is_static_and_preserves_metadata() -> None:
    @nh.function
    def workflow() -> int:
        if False:
            result: Annotated[int, Field(ge=0, le=10)]
        """execute
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": "7"}}
        """
        return result  # type: ignore[name-defined]  # noqa: F821

    with nh.run(StubExecutor()):
        assert workflow() == 7


def test_ambiguous_annotations_fail_preparation_before_executor() -> None:
    @nh.function
    def workflow(condition: bool) -> object:
        if condition:
            result: int = 0  # pyright: ignore[reportRedeclaration, reportAssignmentType]
        else:
            result: str = ""
        """execute
        <:result>
        """
        return result

    events: list[StepFinished] = []
    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=events.append)),
        pytest.raises(nh.ExecutionError, match="Ambiguous annotation"),
    ):
        workflow(True)
    assert len(events) == 1
    assert isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"


def test_unresolved_annotation_fails_preparation_with_identity() -> None:
    @nh.function
    def workflow() -> object:
        result: UnavailableContract = "initial"  # type: ignore[name-defined]  # noqa: F821
        """execute
        <:result>
        """
        return result

    events: list[StepFinished] = []
    with (
        nh.run(StubExecutor()),
        nh.scope(lifecycle=StepLifecycle(on_step_finished=events.append)),
        pytest.raises(nh.ExecutionError, match="UnavailableContract"),
    ):
        workflow()
    assert len(events) == 1
    assert isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"
    assert events[0].execution_reference.step_execution_id is not None


def test_nested_scope_annotations_do_not_override_output_contract() -> None:
    @nh.function
    def workflow() -> int:
        result: int = 0

        def helper() -> str:
            result: str = "nested"
            return result

        class Nested:
            result: str = "nested"

        """execute
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": "7"}}
        """
        return result

    with nh.run(StubExecutor()):
        assert workflow() == 7


def test_explicit_none_output_is_a_contract() -> None:
    @nh.function
    def workflow() -> None:
        result: None = None
        """execute
        <:result>
        {"step_outcome": {"kind": "pass"}, "bindings": {"result": 1}}
        """
        return result

    with nh.run(StubExecutor()), pytest.raises(nh.ExecutionError, match="None"):
        workflow()


def test_frontmatter_deny_return_recognizes_leading_blank_lines():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.function
        def f() -> int:
            result = 0
            """execute

            ---
            deny:
              - return
            ---
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 11}}
            """
            return result

        with pytest.raises(ExecutionError, match="not allowed"):
            f()


def test_frontmatter_deny_return_allows_bindings():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.function
        def f(x: int):
            computed_result = x + 1
            envelope_json_text = json.dumps(
                {
                    "step_outcome": {"kind": "pass"},
                    "bindings": {"result": computed_result},
                }
            )

            f"""execute
            ---
            deny:
              - return
            ---
            <:result>
            {envelope_json_text}
            """
            _ = x
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f(10) == 11


def test_scope_task_references_are_merged_into_step_context() -> None:
    from nighthawk.testing import CallbackExecutor, pass_response

    observed_task_reference_name_set: set[str] = set()

    def search_repository(query: str) -> list[str]:
        _ = query
        return ["hit"]

    def handler(call):  # type: ignore[no-untyped-def]
        observed_task_reference_name_set.update(call.task_name_to_value.keys())
        return pass_response()

    executor = CallbackExecutor(handler)

    with nh.run(executor), nh.scope(task_references={"search_repository": search_repository}):

        @nh.function
        def f() -> None:
            """execute
            Execute one step.
            """

        f()

    assert "search_repository" in observed_task_reference_name_set


def test_unannotated_binding_type_inferred_from_initial_value():
    from nighthawk.testing import ScriptedExecutor, pass_response

    executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"result": "hello"})])
    with nh.run(executor):

        @nh.function
        def f() -> str:
            result = ""
            """execute
            <:result>
            This is a docstring Natural block.
            """

            return result

        assert f() == "hello"

    assert executor.calls[0].binding_name_to_type["result"] is str
