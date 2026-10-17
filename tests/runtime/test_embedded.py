from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, ForwardRef

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFinished, StepLifecycle
from nighthawk.runtime import Pass, Return, StepCandidate, StepDefinition, StepRequest, eval_expression, execute_step
from nighthawk.runtime.step_result import StepResult
from nighthawk.testing import ScriptedExecutor, pass_response
from tests.support.executors import InspectingExecutor, ModelResponseExecutor


@pytest.mark.asyncio
async def test_explicit_environment_is_shared_by_reads_evaluation_discovery_and_types(execute: Callable[..., Awaitable[StepResult]]) -> None:
    value = "caller"
    CallerType = int  # noqa: N806
    shared: list[int] = []
    name_to_value: dict[str, object] = {"value": 7, "CallerType": str, "shared": shared, "result": ""}
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    def inspect(request: StepRequest) -> StepCandidate:
        environment = request.step_context.environment
        assert environment.resolve("value") == 7
        assert environment.resolve("shared") is shared
        assert request.binding_name_to_type == {"result": str}
        assert request.return_annotation == list[str]
        assert eval_expression(request.step_context, "(lambda: value)()") == 7
        assert eval_expression(request.step_context, "list(value + i for i in range(2))") == [7, 8]
        assert environment.describe("value")["origin"] == "python"
        with pytest.raises(NameError):
            environment.resolve("saved_name_to_value")
        assert "saved_name_to_value" not in request.step_context.step_globals
        return StepCandidate(outcome=Return(["done"]), binding_name_to_value={"result": "saved"})

    with nh.run(InspectingExecutor(inspect)), nh.scope(lifecycle=StepLifecycle(events.append)):
        outcome = await execute(
            StepDefinition(
                "Read <value> and write <:result>.",
                input_binding_names=("value", "shared"),
                output_binding_names=("result",),
                binding_name_to_type={"result": "CallerType"},
                return_annotation=list["CallerType"],
                source_location="trusted_program:12",
            ),
            name_to_value=name_to_value,
            write_binding=saved_name_to_value.__setitem__,
        )
    assert outcome == Return(["done"])
    assert value == "caller" and CallerType is int
    assert name_to_value["result"] == ""
    assert saved_name_to_value == {"result": "saved"}
    assert len(events) == 1 and isinstance(events[0], StepCompleted)
    assert events[0].assigned_binding_name_to_value == saved_name_to_value
    assert events[0].input_binding_name_to_value == {"value": 7, "shared": shared}
    assert events[0].execution_reference.source_location == "trusted_program:12"


@pytest.mark.asyncio
async def test_empty_namespace_keeps_builtins_and_scoped_references(execute: Callable[..., Awaitable[StepResult]]) -> None:
    def inspect(request: StepRequest) -> StepCandidate:
        assert request.step_context.step_locals == {}
        assert eval_expression(request.step_context, "len([injected])") == 1
        return StepCandidate(outcome=Pass())

    with nh.run(InspectingExecutor(inspect)), nh.scope(task_references={"injected": 42}):
        assert await execute(StepDefinition("Inspect the environment."), name_to_value={}, write_binding=lambda name, value: None) == Pass()


@pytest.mark.asyncio
async def test_type_contracts_respect_explicit_and_scoped_names(execute: Callable[..., Awaitable[StepResult]]) -> None:
    saved_name_to_value: dict[str, object] = {}
    definition = StepDefinition(
        "Resolve types in the effective environment.",
        output_binding_names=("selected", "nested", "shadowed", "scoped"),
        binding_name_to_type={
            "selected": "SelectedType",
            "nested": "list['SelectedType']",
            "shadowed": "int",
            "scoped": ForwardRef("ScopedType"),
        },
        return_annotation="list['ScopedType']",
    )

    def inspect(request: StepRequest) -> StepCandidate:
        assert request.binding_name_to_type == {"selected": int, "nested": list[int], "shadowed": str, "scoped": bytes}
        assert request.return_annotation == list[bytes]
        return StepCandidate(
            outcome=Return(["done"]),
            binding_name_to_value={"selected": "7", "nested": ["8"], "shadowed": "9", "scoped": "value"},
        )

    with nh.run(InspectingExecutor(inspect)), nh.scope(task_references={"SelectedType": float, "ScopedType": bytes}):
        outcome = await execute(definition, name_to_value={"SelectedType": int, "int": str}, write_binding=saved_name_to_value.__setitem__)
    assert outcome == Return([b"done"])
    assert saved_name_to_value == {"selected": 7, "nested": [8], "shadowed": "9", "scoped": b"value"}


@pytest.mark.asyncio
async def test_nested_natural_uses_own_frame_and_preserves_scope(execute: Callable[..., Awaitable[StepResult]]) -> None:
    events: list[StepFinished] = []

    @nh.natural_function
    def child(value: int) -> int:
        result = 0
        """natural
        Read <value> and write <:result>.
        """
        return result

    def inspect(request: StepRequest) -> StepCandidate:
        if request.processed_natural_program == "parent":
            assert eval_expression(request.step_context, "child(99)") == 99
            assert request.step_context.environment.resolve("value") == 7
            return StepCandidate(outcome=Pass())
        assert request.step_context.environment.resolve("value") == 99
        assert request.step_context.environment.resolve("result") == 0
        assert request.step_context.environment.resolve("shared_policy") == "explicit policy"
        assert request.step_context.environment.describe("shared_policy")["origin"] == "task"
        assert nh.get_lifecycle() is not None
        return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 99})

    with nh.run(InspectingExecutor(inspect)), nh.scope(lifecycle=StepLifecycle(events.append), task_references={"shared_policy": "explicit policy"}):
        await execute(
            StepDefinition("parent"),
            name_to_value={"value": 7, "result": "outer", "child": child, "shared_policy": "parent policy"},
            write_binding=lambda name, value: None,
        )
    assert len(events) == 2
    assert events[0].execution_reference.step_execution_id != events[1].execution_reference.step_execution_id


@pytest.mark.asyncio
async def test_explicit_step_cannot_inherit_parent_temporary(execute: Callable[..., Awaitable[StepResult]]) -> None:
    def inspect(request: StepRequest) -> StepCandidate:
        request.step_context.record_assignment("parent_only", 10)
        with pytest.raises(nh.ExecutionError, match="parent_only"):
            execute_step(StepDefinition("child", input_binding_names=("parent_only",)), name_to_value={}, write_binding=lambda name, value: None)
        return StepCandidate(outcome=Pass())

    with nh.run(InspectingExecutor(inspect)):
        await execute(StepDefinition("parent"), name_to_value={}, write_binding=lambda name, value: None)


@pytest.mark.asyncio
async def test_callable_keeps_its_closure(execute: Callable[..., Awaitable[StepResult]]) -> None:
    captured = 41

    def helper() -> int:
        return captured + 1

    def inspect(request: StepRequest) -> StepCandidate:
        assert eval_expression(request.step_context, "helper()") == 42
        return StepCandidate(outcome=Pass())

    with nh.run(InspectingExecutor(inspect)):
        await execute(StepDefinition("call helper"), name_to_value={"helper": helper, "captured": -1}, write_binding=lambda name, value: None)


@pytest.mark.asyncio
async def test_application_model_and_explicit_types(execute: Callable[..., Awaitable[StepResult]]) -> None:
    class Selection(BaseModel):
        label: str

    saved_name_to_value: dict[str, object] = {}
    binding_name_to_type = {"selected": "Selection", "wide": object, "anything": Any, "empty": None}
    candidate = StepCandidate(outcome=Pass(), binding_name_to_value={"selected": {"label": "chosen"}, "wide": 7, "anything": [1], "empty": None})
    with nh.run(InspectingExecutor(lambda request: candidate)):
        await execute(
            StepDefinition("select", output_binding_names=tuple(binding_name_to_type), binding_name_to_type=binding_name_to_type),
            name_to_value={"Selection": Selection, "wide": "", "anything": "", "empty": 7},
            write_binding=saved_name_to_value.__setitem__,
        )
    assert saved_name_to_value == {"selected": Selection(label="chosen"), "wide": 7, "anything": [1], "empty": None}


@pytest.mark.asyncio
async def test_async_writer_is_awaited_even_by_sync_entry(execute: Callable[..., Awaitable[StepResult]]) -> None:
    order: list[str] = []

    async def write(name: str, value: object) -> None:
        await asyncio.sleep(0)
        order.append(name)

    with nh.run(ScriptedExecutor([pass_response(first=1, second=2)])), nh.scope(lifecycle=StepLifecycle(lambda event: order.append("finished"))):
        await execute(StepDefinition("write", output_binding_names=("first", "second")), name_to_value={}, write_binding=write)
    assert order == ["first", "second", "finished"]


@pytest.mark.asyncio
async def test_exception_expression_resolves_explicit_type(execute: Callable[..., Awaitable[StepResult]]) -> None:
    from nighthawk.runtime.step_contract import RaiseStepOutcome

    executor = ModelResponseExecutor(RaiseStepOutcome(kind="raise", raise_message="expected", raise_error_type="SelectedError"))
    with nh.run(executor), pytest.raises(LookupError, match="expected"):
        await execute(StepDefinition("raise"), name_to_value={"SelectedError": LookupError}, write_binding=lambda name, value: None)


@pytest.mark.asyncio
async def test_preparation_freezes_associations_but_keeps_objects_live(execute: Callable[..., Awaitable[StepResult]]) -> None:
    shared: list[int] = []
    name_to_value: dict[str, object] = {"result": 0, "shared": shared}
    output_binding_names = ["result"]
    saved_name_to_value: dict[str, object] = {}

    def inspect(request: StepRequest) -> StepCandidate:
        name_to_value["result"] = "later"
        output_binding_names.clear()
        shared.append(1)
        assert request.step_context.environment.resolve("result") == 0
        assert request.step_context.environment.resolve("shared") is shared
        return StepCandidate(outcome=Pass(), binding_name_to_value={"result": "7"})

    with nh.run(InspectingExecutor(inspect)):
        await execute(
            StepDefinition("write", output_binding_names=output_binding_names),
            name_to_value=name_to_value,
            write_binding=saved_name_to_value.__setitem__,
        )
    assert saved_name_to_value == {"result": 7}
    assert shared == [1]


@pytest.mark.parametrize("outcome", [nh.runtime.Break(), nh.runtime.Continue(), Return("12")])
@pytest.mark.asyncio
async def test_control_results_are_returned_after_saving(execute: Callable[..., Awaitable[StepResult]], outcome: StepResult) -> None:
    saved_name_to_value: dict[str, object] = {}
    with nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=outcome, binding_name_to_value={"result": 7}))):
        result = await execute(
            StepDefinition("control", output_binding_names=("result",), is_in_loop=True, return_annotation=int),
            name_to_value={},
            write_binding=saved_name_to_value.__setitem__,
        )
    assert result == (Return(12) if isinstance(outcome, Return) else outcome)
    assert saved_name_to_value == {"result": 7}


@pytest.mark.asyncio
async def test_deny_frontmatter_applies_before_saving(execute: Callable[..., Awaitable[StepResult]]) -> None:
    saved_name_to_value: dict[str, object] = {}
    with nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=Pass()))), pytest.raises(nh.ExecutionError, match="not allowed"):
        await execute(StepDefinition("---\ndeny: [pass]\n---\nReturn."), name_to_value={}, write_binding=saved_name_to_value.__setitem__)
    assert saved_name_to_value == {}
