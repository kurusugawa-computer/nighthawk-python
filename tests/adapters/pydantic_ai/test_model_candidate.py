from __future__ import annotations

import pytest

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.model_candidate import resolve_model_candidate
from nighthawk.adapters.pydantic_ai.model_outcome import ModelRaise, ModelReturn
from nighthawk.core import oversight
from nighthawk.core.async_bridge import run_coroutine_synchronously
from nighthawk.core.errors import ExecutionError
from nighthawk.core.operation_contract import OperationRequest
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.errors import NighthawkError
from tests.support.executors import ModelResponseExecutor, StubExecutor


@pytest.mark.parametrize("error_type", ["MissingException", "invalid_exception"])
@pytest.mark.parametrize("decision", [oversight.Accept(), oversight.Rewrite(outcome=Pass())])
def test_invalid_raise_type_fails_before_inspection(error_type: str, decision: oversight.StepCommitDecision) -> None:
    inspected: list[oversight.StepCommit] = []
    invalid_exception = int  # noqa: F841

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return run_coroutine_synchronously(
                lambda: resolve_model_candidate(request, ModelRaise(kind="raise", raise_message="failed", raise_error_type=error_type))
            )

    def inspect(commit: oversight.StepCommit) -> oversight.StepCommitDecision:
        inspected.append(commit)
        return decision

    @nh.function
    def workflow() -> int:
        nonlocal invalid_exception
        """execute
        <invalid_exception>
        Raise an error.
        """
        return 7

    with (
        nh.run(Executor()),
        nh.scope(oversight=oversight.Oversight(inspect_step_commit=inspect)),
        pytest.raises(ExecutionError, match="Invalid raise_error_type"),
    ):
        workflow()
    assert inspected == []


@pytest.mark.asyncio
async def test_asynchronous_natural_awaits_model_return_expression() -> None:
    class AssertingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            raise AssertionError("run_step should not be used for this async test")

        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context

            async def calculate() -> int:
                return 17

            step_context.environment.assign("calculate", calculate)
            return await resolve_model_candidate(request, ModelReturn(kind="return", return_expression="calculate()"))

    with nh.run(AssertingExecutor()):

        @nh.function
        async def f() -> int:
            """execute
            return the value.
            """
            return 0

        assert await f() == 17


def test_synchronous_natural_rejects_awaitable_model_return_expression():
    class AwaitableInt:
        def __await__(self):  # type: ignore[no-untyped-def]
            if False:
                yield None
            return 17

    class AssertingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            step_context.environment.assign("result", AwaitableInt())
            return run_coroutine_synchronously(lambda: resolve_model_candidate(request, ModelReturn(kind="return", return_expression="result")))

    with nh.run(AssertingExecutor()):

        @nh.function
        def f() -> int:
            """execute
            return the value.
            """
            return 0

        with pytest.raises(NighthawkError, match="awaitable"):
            f()


def test_model_return_expression_resolves_proposed_binding():
    with nh.run(StubExecutor()):

        @nh.function
        def f() -> int:
            """execute
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 11}}
            """
            result = 0
            return result

        assert f() == 11


def test_model_return_expression_rejects_unbound_name():
    with nh.run(StubExecutor()):

        @nh.function
        def f() -> int:
            """execute
            {"step_outcome": {"kind": "return", "return_expression": "missing"}, "bindings": {}}
            """
            return 0

        with pytest.raises(ExecutionError):
            f()


def test_denied_return_never_calls_inspector_or_evaluator() -> None:
    calls: list[str] = []

    def inspect_expression(request: OperationRequest) -> oversight.Accept:
        calls.append("inspected")
        return oversight.Accept()

    @nh.function
    def workflow() -> int:
        nonlocal calls
        """execute
        ---
        deny: [return]
        ---
        <calls>
        ignored
        """
        return 0

    with (
        nh.run(ModelResponseExecutor(ModelReturn(kind="return", return_expression="calls.append('evaluated')"))),
        nh.scope(oversight=oversight.Oversight(inspect_operation=inspect_expression)),
        pytest.raises(ExecutionError),
    ):
        workflow()
    assert calls == []
