from __future__ import annotations

import pytest

from nighthawk.runtime import OperationError, eval_expression, eval_expression_async
from tests.support.contexts import new_context as context_for


@pytest.mark.parametrize("exception_type", [NameError, ValueError, TypeError])
def test_helper_failures_propagate_without_retry_conversion(exception_type: type[Exception]) -> None:
    error = exception_type("application failure")

    def fail() -> None:
        raise error

    with pytest.raises(exception_type) as caught:
        eval_expression(context_for(fail=fail), "fail()")
    assert caught.value is error


def test_lookup_failure_is_recoverable_and_named_assignment_is_rejected() -> None:
    context = context_for()
    with pytest.raises(OperationError) as caught:
        eval_expression(context, "missing")
    assert caught.value.phase == "evaluation"
    with pytest.raises(OperationError) as caught:
        eval_expression(context, "(value := 7)")
    assert caught.value.phase == "admission"
    assert not caught.value.evaluation_started


def test_rejected_sync_return_does_not_close_application_coroutine() -> None:
    import asyncio
    import inspect

    from nighthawk.errors import NighthawkError
    from nighthawk.runtime.expression import compile_expression, evaluate_compiled_expression

    async def application_value() -> int:
        return 9

    retained = application_value()
    try:
        with pytest.raises(NighthawkError, match="cannot return an awaitable"):
            asyncio.run(evaluate_compiled_expression(context_for(retained=retained), compile_expression("retained"), allow_awaitable_result=False))
        assert inspect.getcoroutinestate(retained) == inspect.CORO_CREATED
        assert asyncio.run(retained) == 9
    finally:
        retained.close()


def test_discarded_core_await_wrapper_is_closed_without_running_application() -> None:
    import asyncio
    import gc
    import warnings

    from nighthawk.errors import NighthawkError
    from nighthawk.runtime.expression import compile_expression, evaluate_compiled_expression

    effects: list[str] = []

    async def application_value() -> int:
        effects.append("entered")
        return 9

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(NighthawkError):
            asyncio.run(
                evaluate_compiled_expression(
                    context_for(application_value=application_value), compile_expression("await application_value()"), allow_awaitable_result=False
                )
            )
        gc.collect()
    assert effects == []
    assert not any("was never awaited" in str(warning.message) for warning in captured)


def test_expression_failure_coroutine_origin_belongs_to_application_helper() -> None:
    import asyncio
    import gc
    import inspect
    import sys
    import warnings

    from nighthawk.runtime.expression import compile_expression, evaluate_compiled_expression

    async def value() -> int:
        return 1

    retained: list[object] = []

    def create():
        coroutine = value()
        retained.append(coroutine)
        return coroutine

    previous_depth = sys.get_coroutine_origin_tracking_depth()
    sys.set_coroutine_origin_tracking_depth(5)
    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            with pytest.raises(ZeroDivisionError):
                asyncio.run(evaluate_compiled_expression(context_for(create=create), compile_expression("(create(), 1 / 0)")))
            coroutine = retained.pop()
            assert inspect.iscoroutine(coroutine)
            assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CREATED
            assert coroutine.cr_origin is not None
            assert coroutine.cr_origin[0][2] == "create"
            coroutine.close()
            gc.collect()
        assert not any("was never awaited" in str(warning.message) for warning in captured)
    finally:
        sys.set_coroutine_origin_tracking_depth(previous_depth)


def test_nested_awaitable_remains_application_value_after_one_dsl_await() -> None:
    import asyncio
    import inspect

    from nighthawk.runtime.expression import compile_expression, evaluate_compiled_expression

    async def inner() -> int:
        return 3

    retained = inner()

    async def outer():
        return retained

    try:
        result = asyncio.run(evaluate_compiled_expression(context_for(outer=outer), compile_expression("outer()")))
        assert result is retained
        assert inspect.getcoroutinestate(retained) == inspect.CORO_CREATED
        assert asyncio.run(retained) == 3
    finally:
        retained.close()


@pytest.mark.asyncio
async def test_eval_expression_async_supports_top_level_await() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = context_for()
    step_context.record_assignment("calculate", calculate)

    result = await eval_expression_async(step_context, "await calculate(1, 2)")
    assert result == 17


@pytest.mark.asyncio
async def test_eval_expression_async_awaits_returned_awaitable_implicitly() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = context_for()
    step_context.record_assignment("calculate", calculate)

    result = await eval_expression_async(step_context, "calculate(1, 2)")
    assert result == 17
