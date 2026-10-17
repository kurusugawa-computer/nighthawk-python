from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFailed, StepFinished, StepLifecycle
from nighthawk.runtime import (
    OperationError,
    Pass,
    Return,
    StepCandidate,
    StepDefinition,
    StepEnvironment,
    StepRequest,
    StepResult,
    eval_expression_async,
)
from tests.support.prompts import build_user_prompt_text

_module_value = object()


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous_child", [False, True])
async def test_embedded_children_and_grandchildren_only_receive_explicit_values(
    execute: Callable[..., Awaitable[StepResult]], asynchronous_child: bool
) -> None:
    shared: list[int] = []
    child_cell = object()
    contexts: list[nh.StepContext] = []
    events: list[StepFinished] = []

    @nh.natural_function
    def child(value: list[int]) -> None:
        nonlocal child_cell
        child_local = 11  # noqa: F841 - read by Natural.
        """natural
        Child: inspect <value>, <child_cell>, <child_local>, and <_module_value>.
        """

    @nh.natural_function
    async def asynchronous_child_function(value: list[int]) -> None:
        nonlocal child_cell
        child_local = 11  # noqa: F841 - read by Natural.
        """natural
        Child: inspect <value>, <child_cell>, <child_local>, and <_module_value>.
        """

    @nh.natural_function
    def grandchild(value: list[int]) -> None:
        """natural
        Grandchild: inspect <value>.
        """

    @nh.natural_function
    async def asynchronous_grandchild(value: list[int]) -> None:
        """natural
        Grandchild: inspect <value>.
        """

    def select_shared_references(environment: StepEnvironment) -> dict[str, object]:
        return {"shared_value": environment.resolve("selected")}

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            context = request.step_context
            contexts.append(context)
            environment = context.environment
            if request.processed_natural_program == "parent":
                context.record_assignment("parent_temporary", "PARENT_TEMPORARY_MARKER")
                with nh.scope(task_references=select_shared_references(environment)):
                    assert nh.get_execution_reference().step_execution_id == context.execution_reference.step_execution_id
                    if asynchronous_child:
                        await asynchronous_child_function(shared)
                    else:
                        child(shared)
                assert nh.get_step_context() is context
                assert environment.resolve("parent_input") == "PARENT_INPUT_MARKER"
                assert environment.resolve("parent_temporary") == "PARENT_TEMPORARY_MARKER"
                assert environment.task_name_to_value == {}
                assert shared == [1]
                return StepCandidate(outcome=Pass())

            assert environment.resolve("value") is shared
            assert environment.resolve("shared_value") is shared
            assert environment.describe("shared_value")["origin"] == "task"
            assert environment.resolve("_module_value") is _module_value
            assert environment.resolve("len") is len
            assert not hasattr(environment, "parent_name_to_value")
            forbidden_names = {"parent_input", "parent_temporary", "selected", "child_temporary"}
            for name in forbidden_names:
                with pytest.raises(NameError):
                    environment.resolve(name)
                assert environment.describe(name)["origin"] is None
            with pytest.raises(ValueError, match="Unknown environment source"):
                environment.resolve("parent_input", source="parent")  # type: ignore[arg-type]
            assert "error" in environment.discover(source="parent")  # type: ignore[arg-type]

            discovered_names: set[str] = set()
            page: Any = environment.discover(max_items=11)
            while True:
                discovered_names.update(item["name"] for item in page["names"])
                if page["next_cursor"] is None:
                    break
                page = await eval_expression_async(context, page["continuation"])
            assert not discovered_names & forbidden_names
            for expression in ("parent_input", "(lambda: parent_temporary)()", "list(parent_input for i in range(1))"):
                with pytest.raises(OperationError) as caught:
                    await eval_expression_async(context, expression)
                assert isinstance(caught.value.__cause__, NameError)
            assert await eval_expression_async(context, "(lambda: shared_value)()") is shared
            prompt = build_user_prompt_text(processed_natural_program=request.processed_natural_program, step_context=context)
            assert "PARENT_INPUT_MARKER" not in prompt
            assert "PARENT_TEMPORARY_MARKER" not in prompt
            assert "CHILD_TEMPORARY_MARKER" not in prompt
            assert not any(name in prompt for name in forbidden_names)

            if request.processed_natural_program.startswith("Child:"):
                assert environment.resolve("child_cell") is child_cell
                assert environment.resolve("child_local") == 11
                context.record_assignment("child_temporary", "CHILD_TEMPORARY_MARKER")
                if asynchronous_child:
                    await asynchronous_grandchild(shared)
                else:
                    grandchild(shared)
                assert environment.resolve("child_temporary") == "CHILD_TEMPORARY_MARKER"
            else:
                with pytest.raises(NameError):
                    environment.resolve("child_cell")
                shared.append(1)
            return StepCandidate(outcome=Pass())

    with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(on_step_finished=events.append)):
        await execute(
            StepDefinition("parent", input_binding_names=("parent_input",)),
            name_to_value={"parent_input": "PARENT_INPUT_MARKER", "selected": shared, "child_cell": "PARENT_CELL_MARKER"},
            write_binding=lambda name, value: pytest.fail("No writes were declared"),
        )
    assert len(contexts) == len(events) == 3
    assert all(isinstance(event, StepCompleted) for event in events)
    for parent, descendant in zip(contexts, contexts[1:], strict=False):
        assert descendant.execution_reference.run_id == parent.execution_reference.run_id
        assert descendant.execution_reference.parent_step_execution_id == parent.execution_reference.step_execution_id


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_missing_parent_read_fails_before_executor_and_writeback_through_call_handler(asynchronous: bool) -> None:
    result = 0
    events: list[StepFinished] = []
    calls: list[nh.FunctionCall] = []
    requests: list[StepRequest] = []

    @nh.natural_function
    def child() -> None:
        nonlocal result
        """natural
        Read <parent_input> and <parent_temporary>, then write <:result>.
        """

    @nh.natural_function
    async def asynchronous_child() -> None:
        nonlocal result
        """natural
        Read <parent_input> and <parent_temporary>, then write <:result>.
        """

    child_alias = asynchronous_child if asynchronous else child

    @nh.natural_function
    def parent(parent_input: str) -> None:
        """natural
        Parent: inspect <parent_input>.
        """

    @nh.natural_function
    async def asynchronous_parent(parent_input: str) -> None:
        """natural
        Parent: inspect <parent_input>.
        """

    class Handler:
        def invoke(self, call: nh.FunctionCall, proceed: Callable[[], object]) -> object:
            calls.append(call)
            with nh.scope(task_references={}):
                return proceed()

        async def invoke_async(self, call: nh.FunctionCall, proceed: Callable[[], Awaitable[object]]) -> object:
            calls.append(call)
            with nh.scope(task_references={}):
                return await proceed()

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            requests.append(request)
            assert request.processed_natural_program.startswith("Parent:")
            request.step_context.record_assignment("parent_temporary", 42)
            with pytest.raises(nh.ExecutionError) as caught:
                if asynchronous:
                    await child_alias()  # type: ignore[misc]
                else:
                    child_alias()
            assert isinstance(caught.value.__cause__, NameError)
            assert request.step_context.environment.resolve("parent_input") == "parent"
            assert request.step_context.environment.resolve("parent_temporary") == 42
            return StepCandidate(outcome=Pass())

    with nh.run(Executor()), nh.scope(lifecycle=StepLifecycle(on_step_finished=events.append)), nh.function_call_scope(Handler()):
        if asynchronous:
            await asynchronous_parent("parent")
        else:
            parent("parent")
    assert result == 0
    assert len(requests) == 1
    assert len(calls) == len(events) == 2
    assert calls[1].parent_call_id == calls[0].call_id
    assert isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"
    assert isinstance(events[0].original_exception, NameError)
    assert isinstance(events[1], StepCompleted)
    assert events[0].execution_reference.parent_step_execution_id == events[1].execution_reference.step_execution_id


@pytest.mark.asyncio
async def test_replaced_or_cleared_references_do_not_reappear_from_parent_working_values() -> None:
    shared = object()
    replacement = object()

    @nh.natural_function
    async def child(expected: object) -> None:
        """natural
        Child: inspect <expected>.
        """

    @nh.natural_function
    async def parent() -> None:
        """natural
        Parent: inspect <shared_value>.
        """

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            environment = request.step_context.environment
            if request.processed_natural_program.startswith("Parent:"):
                request.step_context.record_assignment("shared_value", "parent working value")
                await child(shared)
                with nh.scope(task_references={"shared_value": replacement}):
                    await child(replacement)
                with nh.scope(task_references={}):
                    await child(None)
                with pytest.raises(ValueError), nh.scope(task_references={}):
                    raise ValueError("restore references")
                await child(shared)
                assert environment.resolve("shared_value") == "parent working value"
            else:
                expected = environment.resolve("expected")
                if expected is None:
                    with pytest.raises(NameError):
                        environment.resolve("shared_value")
                else:
                    assert environment.resolve("shared_value") is expected
            return StepCandidate(outcome=Pass())

    with nh.run(Executor()), nh.scope(task_references={"shared_value": shared}):
        await parent()


@pytest.mark.asyncio
async def test_async_reference_context_timing_concurrency_and_cancellation() -> None:
    first, second, blocked = object(), object(), object()
    entered, cleaned = asyncio.Event(), asyncio.Event()

    @nh.natural_function
    async def child() -> object:
        """natural
        Return <shared_value>.
        """

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            value = request.step_context.environment.resolve("shared_value")
            if value is blocked:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            await asyncio.sleep(0)
            return StepCandidate(outcome=Return(value))

    async def worker(value: object) -> object:
        with nh.scope(task_references={"shared_value": value}):
            return await child()

    with nh.run(Executor()):
        with nh.scope(task_references={"shared_value": first}):
            coroutine = child()
            task = asyncio.create_task(child())
        with nh.scope(task_references={"shared_value": second}):
            assert await coroutine is second
            assert await task is first
            results = await asyncio.gather(worker(first), worker(second))
            assert results[0] is first and results[1] is second
            interrupted = asyncio.create_task(worker(blocked))
            await entered.wait()
            interrupted.cancel()
            with pytest.raises(asyncio.CancelledError):
                await interrupted
            assert cleaned.is_set()
            assert await child() is second
        with pytest.raises(nh.ExecutionError) as caught:
            await child()
        assert isinstance(caught.value.__cause__, NameError)
