from __future__ import annotations

from typing import Any

import pytest

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepRequest
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.model_candidate import resolve_model_candidate
from nighthawk.runtime.step_contract import ReturnStepOutcome
from nighthawk.tools.assignment import eval_expression
from tests.execution.stub_executor import StubExecutor

_module_value = 99


def test_natural_text_does_not_capture_outer_values() -> None:
    value = 5

    @nh.natural_function
    def workflow() -> int:
        result: int = 0
        """natural
        <value> <:result>
        {"step_outcome": {"kind": "return", "return_expression": "value"}, "bindings": {}}
        """
        return result

    value = 7
    assert value == 7
    assert "value" not in nh.get_transformed_function(workflow).__code__.co_freevars
    with nh.run(StubExecutor()):
        with pytest.raises(nh.ExecutionError) as caught:
            workflow()
        assert isinstance(caught.value.__cause__, NameError)
        with nh.scope(implicit_references={"value": 8}):
            assert workflow() == 8


def test_explicit_cell_observes_rebinding_and_empty_cells() -> None:
    value = 5

    @nh.natural_function
    def workflow() -> int:
        nonlocal value
        """natural
        <value>
        {"step_outcome": {"kind": "return", "return_expression": "value"}, "bindings": {}}
        """
        return 0

    value = 7
    with nh.run(StubExecutor()), nh.scope(implicit_references={"value": 8}):
        assert workflow() == 7
        del value
        with pytest.raises(nh.ExecutionError) as caught:
            workflow()
        assert isinstance(caught.value.__cause__, UnboundLocalError)


def test_local_classification_blocks_injected_fallback_without_explicit_read() -> None:
    @nh.natural_function
    def workflow() -> int:
        """natural
        {"step_outcome": {"kind": "return", "return_expression": "value"}, "bindings": {}}
        """
        value = 99
        return value

    with nh.run(StubExecutor()), nh.scope(implicit_references={"value": 8}), pytest.raises(nh.ExecutionError) as caught:
        workflow()
    assert caught.value.__cause__ is not None
    assert isinstance(caught.value.__cause__.__cause__, UnboundLocalError)


def test_child_without_cells_does_not_borrow_parent_python_cells() -> None:
    _module_value = 5

    @nh.natural_function
    def child() -> int:
        """natural
        Read the value.
        """
        return 0

    @nh.natural_function
    def parent() -> int:
        nonlocal child, _module_value
        """natural
        Call the child.
        """
        return 0

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            expression = "child()" if "Call the child" in request.processed_natural_program else "_module_value"
            return run_coroutine_synchronously(
                lambda: resolve_model_candidate(request, ReturnStepOutcome(kind="return", return_expression=expression), {})
            )

    with nh.run(Executor()):
        assert parent() == 99


def test_original_application_helper_keeps_its_own_closure() -> None:
    value = 3

    def helper() -> int:
        return value

    @nh.natural_function
    def workflow(value: int) -> int:
        nonlocal helper
        """natural
        <helper>
        {"step_outcome": {"kind": "return", "return_expression": "helper() + value"}, "bindings": {}}
        """
        return 0

    value = 4
    with nh.run(StubExecutor()):
        assert workflow(99) == 103


def test_defaults_run_once_and_retain_live_objects() -> None:
    defaults: list[list[int]] = []

    def make_default() -> list[int]:
        value = [len(defaults)]
        defaults.append(value)
        return value

    @nh.natural_function
    def workflow(value: list[int] = make_default(), *, other: list[int] = make_default()) -> tuple[list[int], list[int]]:  # noqa: B008
        """natural
        {"step_outcome": {"kind": "pass"}, "bindings": {}}
        """
        return value, other

    assert len(defaults) == 2
    with nh.run(StubExecutor()):
        first, second = workflow()
        assert first is defaults[0]
        assert second is defaults[1]
        first.append(9)
        assert workflow()[0] == [0, 9]
    assert len(defaults) == 2


def test_instrumentation_does_not_call_shadowed_dict_or_zip() -> None:
    @nh.natural_function
    def workflow(dict: str, zip: str) -> str:
        """natural
        {"step_outcome": {"kind": "return", "return_expression": "dict + zip"}, "bindings": {}}
        """
        return ""

    with nh.run(StubExecutor()):
        assert workflow("one", "two") == "onetwo"


def test_nested_decorated_function_owns_its_own_cells() -> None:
    @nh.natural_function
    def parent() -> int:
        value = 5

        @nh.natural_function
        def child() -> int:
            nonlocal value
            """natural
            Read <value>.
            """
            return 0

        value = 8
        return child()

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return run_coroutine_synchronously(
                lambda: resolve_model_candidate(request, ReturnStepOutcome(kind="return", return_expression="value"), {})
            )

    with nh.run(Executor()):
        assert parent() == 8


def test_global_rebinding_during_an_operation_is_visible_to_nested_expressions() -> None:
    global _module_value
    original_value = _module_value

    def change_global() -> None:
        global _module_value
        _module_value = 11

    @nh.natural_function
    def workflow() -> None:
        nonlocal change_global
        """natural
        Read the changing global.
        """

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            assert eval_expression(request.step_context, "(change_global(), (lambda: _module_value)())[1]") == 11
            return StepCandidate(outcome=Pass(), binding_name_to_value={})

    try:
        with nh.run(Executor()):
            workflow()
    finally:
        _module_value = original_value


def test_unavailable_source_is_reported_explicitly() -> None:
    namespace: dict[str, Any] = {}
    exec(compile("def generated():\n    pass\n", "<unavailable-natural-source>", "exec"), namespace)
    with pytest.raises(nh.NighthawkError, match="available Python"):
        nh.natural_function(namespace["generated"])
