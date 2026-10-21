from __future__ import annotations

import pytest

import nighthawk as nh
from nighthawk.errors import ExecutionError, NighthawkError
from nighthawk.runtime import StepCandidate, StepRequest
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.model_candidate import resolve_model_candidate
from nighthawk.runtime.model_outcome import ModelPass, ModelRaise, ModelReturn
from tests.support.executors import ModelResponseExecutor, StubExecutor


@pytest.mark.parametrize("error_type", ["MissingException", "invalid_exception"])
@pytest.mark.parametrize("decision", [nh.oversight.Accept(), nh.oversight.Rewrite(outcome=nh.oversight.Pass())])
def test_invalid_raise_type_fails_before_inspection(error_type: str, decision: nh.oversight.StepCommitDecision) -> None:
    inspected: list[nh.oversight.StepCommit] = []
    invalid_exception = int  # noqa: F841

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return run_coroutine_synchronously(
                lambda: resolve_model_candidate(request, ModelRaise(kind="raise", raise_message="failed", raise_error_type=error_type), {})
            )

    def inspect(commit: nh.oversight.StepCommit) -> nh.oversight.StepCommitDecision:
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
        nh.scope(oversight=nh.oversight.Oversight(inspect_step_commit=inspect)),
        pytest.raises(nh.ExecutionError, match="Invalid raise_error_type"),
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

            step_context.record_assignment("calculate", calculate)
            return await resolve_model_candidate(request, ModelReturn(kind="return", return_expression="calculate()"), {})

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
            step_context.record_assignment("result", AwaitableInt())
            return run_coroutine_synchronously(lambda: resolve_model_candidate(request, ModelReturn(kind="return", return_expression="result"), {}))

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


@pytest.mark.parametrize("raising", [False, True])
def test_undeclared_proposals_are_rejected_even_for_raise(raising: bool) -> None:
    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            outcome = ModelRaise(kind="raise", raise_message="failed") if raising else ModelPass(kind="pass")
            return run_coroutine_synchronously(lambda: resolve_model_candidate(request, outcome, {"undeclared": 1}))

    @nh.function
    def workflow() -> None:
        """execute
        Finish.
        """

    with nh.run(Executor()), pytest.raises(nh.ExecutionError, match="Undeclared output"):
        workflow()


def test_denied_return_never_calls_inspector_or_evaluator() -> None:
    calls: list[str] = []

    def inspect_expression(request: nh.runtime.OperationRequest) -> nh.oversight.Accept:
        calls.append("inspected")
        return nh.oversight.Accept()

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
        nh.scope(oversight=nh.oversight.Oversight(inspect_operation=inspect_expression)),
        pytest.raises(nh.ExecutionError),
    ):
        workflow()
    assert calls == []
