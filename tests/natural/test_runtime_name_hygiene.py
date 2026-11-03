from __future__ import annotations

import asyncio

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepRequest, assign_expression_async, evaluate_expression_async


def test_every_source_helper_name_can_be_an_application_binding() -> None:
    @nh.function
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
        __nh_body__ = 13  # noqa: F841 - read by Natural.
        """execute
        Read <__nh_envelope__>, <__nh_bindings__>, <__nh_step_outcome__>, <__nighthawk_runner__>,
        <__nh_cell_anchor__>, <__nh_name_to_cell__>, <__nh_python_cell_scope__>, <__nh_function_name_to_cell__>,
        <__nh_source_annotation__>, <__nh_missing_annotation__>, <__nh_extract_program__>, <__nh_body__>, and <__nh_captured_values__>.
        Increment <:__nh_execution__>.
        """
        f"""execute
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
                "__nh_body__",
            )
            for i, name in enumerate(expected_names, start=1):
                assert environment.resolve(name) == (len(requests) if i == 1 else i)
                assert environment.describe(name)["origin"] == "python"
            assert environment.resolve("__nh_captured_values__") == 17
            assert set(environment.snapshot_working_state()) == {*expected_names, "__nh_captured_values__"}
            assert set(request.step_context.step_locals) == {*expected_names, "__nh_captured_values__"}
            updated = await evaluate_expression_async(request.step_context, "__nh_execution__ + 1")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"__nh_execution__": updated})

    with nh.run(Executor()):
        assert __nh_factory__() == (3, 17)
    assert len(requests) == 2
    assert "original argument was 17" in requests[1].processed_natural_program


def test_factory_capture_names_preserve_live_nonlocal_cells() -> None:
    __nh_captured_values__ = 5

    @nh.function
    async def workflow() -> int:
        nonlocal __nh_captured_values__
        """execute
        Increment <:__nh_captured_values__>.
        """
        return __nh_captured_values__

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            updated = await evaluate_expression_async(request.step_context, "__nh_captured_values__ + 1")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"__nh_captured_values__": updated})

    __nh_captured_values__ = 8
    with nh.run(Executor()), nh.scope(task_references={"__nh_captured_values__": 99}):
        assert asyncio.run(workflow()) == 9
    assert __nh_captured_values__ == 9


def test_child_receives_shared_application_binding_even_when_its_own_helper_has_that_name() -> None:
    @nh.function
    async def child() -> int:
        result = 0
        """execute
        Copy the shared execution value into <:result>.
        """
        return result

    @nh.function
    async def parent() -> int:
        nonlocal child
        __nh_execution__ = 7  # noqa: F841 - explicitly shared by the executor.
        result = 0
        """execute
        Call <child> and store its result in <:result>.
        """
        return result

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            context = request.step_context
            if "shared" in request.processed_natural_program:
                assert context.environment.resolve("__nh_execution__") == 7
                assert context.environment.describe("__nh_execution__")["origin"] == "task"
                await assign_expression_async(context, "result", "__nh_execution__ + 1")
            else:
                with nh.scope(task_references={"__nh_execution__": context.environment.resolve("__nh_execution__")}):
                    assert await evaluate_expression_async(context, "child()") == 8
                assert context.environment.resolve("__nh_execution__") == 7
                await assign_expression_async(context, "result", "__nh_execution__")
            return StepCandidate(outcome=Pass())

    with nh.run(Executor()):
        assert asyncio.run(parent()) == 7
