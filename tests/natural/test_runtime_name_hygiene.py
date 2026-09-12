"""Instrumentation owns generated bindings, never an application name prefix."""

from __future__ import annotations

import asyncio
from types import CellType

import pytest

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepEnvironment, StepRequest, assign_tool_async, eval_expression_async
from tests.support.prompts import build_step_context, build_user_prompt_text


@pytest.mark.parametrize("name", ["__nh_application", "__nighthawk_application", "__nh_execution__"])
@pytest.mark.parametrize("source", ["local", "global", "cell"])
def test_application_prefix_keeps_lexical_priority_and_qualified_access(name: str, source: str) -> None:
    environment = StepEnvironment(
        python_name_to_value={name: None} if source == "local" else {},
        python_globals={name: None} if source == "global" else {},
        python_name_to_cell={name: CellType(None)} if source == "cell" else {},
        parent_name_to_value={name: 5},
        task_name_to_value={name: 9},
        output_name_set=frozenset({name}),
    )
    assert environment.resolve(name) is None
    assert environment.describe(name)["origin"] == "python"
    assert name in environment.python_name_to_value
    namespace = environment.expression_namespace()
    assert eval(environment.reference(name, source="python"), namespace, namespace) is None
    assert environment.resolve(name, source="task") == 9
    environment.assign(name, 7)
    assert environment.resolve(name) == 7
    assert environment.resolve(name, source="python") is None
    assert environment.snapshot_working_state()[name] == 7


@pytest.mark.parametrize("cell", [False, True])
def test_unbound_application_prefix_blocks_fallback(cell: bool) -> None:
    name = "__nh_execution__"
    environment = StepEnvironment(
        python_name_to_value={},
        python_globals={name: 99},
        local_name_set=frozenset() if cell else frozenset({name}),
        python_name_to_cell={name: CellType()} if cell else {},
        parent_name_to_value={name: 5},
        task_name_to_value={name: 9},
    )
    with pytest.raises(UnboundLocalError):
        environment.resolve(name)
    assert environment.describe(name)["unbound"] is True
    assert name not in environment.snapshot_working_state()


def test_explicit_application_prefixes_are_presented_in_initial_context() -> None:
    context = build_step_context(python_globals={"__nh_global": 9}, python_locals={"__nh_local": 7})
    context.environment = StepEnvironment(
        python_name_to_value=context.step_locals, python_globals=context.step_globals, task_name_to_value={"__nighthawk_task": 5}
    )
    prompt = build_user_prompt_text(processed_natural_program="Read <__nh_local>, <__nh_global>, and <__nighthawk_task>.", step_context=context)
    for name, value in (("__nh_local", 7), ("__nh_global", 9), ("__nighthawk_task", 5)):
        assert f"{name}: int = {value}" in prompt


def test_every_source_helper_name_can_be_an_application_binding() -> None:
    @nh.natural_function
    def __nh_factory__(__nh_captured_values__: int = 17) -> tuple[int, int]:  # noqa: N807 - deliberately collides with the factory name.
        __nh_execution__ = 1
        __nh_envelope__ = 2  # noqa: F841 - read by Natural.
        __nh_bindings__ = 3  # noqa: F841 - read by Natural.
        __nh_step_outcome__ = 4  # noqa: F841 - read by Natural.
        __nighthawk_runner__ = 5  # noqa: F841 - read by Natural.
        __nh_cell_anchor__ = 6  # noqa: F841 - read by Natural.
        __nh_name_to_cell__ = 7  # noqa: F841 - read by Natural.
        __nh_python_cell_scope__ = 8  # noqa: F841 - read by Natural.
        __nh_function_name_to_cell__ = 9  # noqa: F841 - read by Natural.
        __nh_source_annotation__ = 10  # noqa: F841 - read by Natural.
        __nh_missing_annotation__ = 11  # noqa: F841 - read by Natural.
        __nh_extract_program__ = 12  # noqa: F841 - read by Natural.
        """natural
        Read <__nh_envelope__>, <__nh_bindings__>, <__nh_step_outcome__>, <__nighthawk_runner__>,
        <__nh_cell_anchor__>, <__nh_name_to_cell__>, <__nh_python_cell_scope__>, <__nh_function_name_to_cell__>,
        <__nh_source_annotation__>, <__nh_missing_annotation__>, <__nh_extract_program__>, and <__nh_captured_values__>.
        Increment <:__nh_execution__>.
        """
        f"""natural
        The original argument was {__nh_captured_values__}.
        Increment <:__nh_execution__> again.
        """
        return __nh_execution__, __nh_captured_values__

    requests: list[StepRequest] = []

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            requests.append(request)
            environment = request.step_context.environment
            expected_names = (
                "__nh_execution__",
                "__nh_envelope__",
                "__nh_bindings__",
                "__nh_step_outcome__",
                "__nighthawk_runner__",
                "__nh_cell_anchor__",
                "__nh_name_to_cell__",
                "__nh_python_cell_scope__",
                "__nh_function_name_to_cell__",
                "__nh_source_annotation__",
                "__nh_missing_annotation__",
                "__nh_extract_program__",
            )
            for i, name in enumerate(expected_names, start=1):
                assert environment.resolve(name) == (len(requests) if i == 1 else i)
                assert environment.describe(name)["origin"] == "python"
            assert environment.resolve("__nh_captured_values__") == 17
            assert set(environment.snapshot_working_state()) == {*expected_names, "__nh_captured_values__"}
            assert set(request.step_context.step_locals) == {*expected_names, "__nh_captured_values__"}
            updated = await eval_expression_async(request.step_context, "__nh_execution__ + 1")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"__nh_execution__": updated})

    with nh.run(Executor()):
        assert __nh_factory__() == (3, 17)
    assert len(requests) == 2
    assert "original argument was 17" in requests[1].processed_natural_program


def test_factory_capture_names_preserve_live_nonlocal_cells() -> None:
    __nh_captured_values__ = 5

    @nh.natural_function
    async def workflow() -> int:
        nonlocal __nh_captured_values__
        """natural
        Increment <:__nh_captured_values__>.
        """
        return __nh_captured_values__

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            updated = await eval_expression_async(request.step_context, "__nh_captured_values__ + 1")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"__nh_captured_values__": updated})

    __nh_captured_values__ = 8
    with nh.run(Executor()), nh.scope(task_references={"__nh_captured_values__": 99}):
        assert asyncio.run(workflow()) == 9
    assert __nh_captured_values__ == 9


def test_child_inherits_application_binding_even_when_its_own_helper_has_that_name() -> None:
    @nh.natural_function
    async def child() -> int:
        result = 0
        """natural
        Copy the inherited execution value into <:result>.
        """
        return result

    @nh.natural_function
    async def parent() -> int:
        nonlocal child
        __nh_execution__ = 7  # noqa: F841 - inherited by child.
        result = 0
        """natural
        Call <child> and store its result in <:result>.
        """
        return result

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            context = request.step_context
            if "inherited" in request.processed_natural_program:
                assert context.environment.resolve("__nh_execution__") == 7
                assert context.environment.describe("__nh_execution__")["origin"] == "parent"
                await assign_tool_async(context, "result", "__nh_execution__ + 1")
            else:
                assert await eval_expression_async(context, "child()") == 8
                assert context.environment.resolve("__nh_execution__") == 7
                await assign_tool_async(context, "result", "__nh_execution__")
            return StepCandidate(outcome=Pass())

    with nh.run(Executor()):
        assert asyncio.run(parent()) == 7
