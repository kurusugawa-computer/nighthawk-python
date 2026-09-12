import asyncio
import json
from dataclasses import dataclass, field
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, model_validator

import nighthawk as nh
from nighthawk.errors import ExecutionError, NighthawkError
from nighthawk.runtime import Pass, StepCandidate, StepRequest
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.model_candidate import resolve_model_candidate
from nighthawk.runtime.step_contract import ReturnStepOutcome
from tests.support.executors import StubExecutor

GLOBAL_NUMBER = 7

SHADOWED_NUMBER = 1


class RuntimeChildModel(BaseModel):
    value: int


class RuntimeResultModel(BaseModel, validate_assignment=True):
    child: RuntimeChildModel | None = None
    value: int = 0


class RuntimeApproveProposal(BaseModel):
    kind: Literal["approve"]
    score: int


class RuntimeRejectProposal(BaseModel):
    kind: Literal["reject"]
    reason: str


RuntimeProposal = Annotated[RuntimeApproveProposal | RuntimeRejectProposal, Field(discriminator="kind")]


class RuntimeProposalEnvelope(BaseModel):
    proposals: list[RuntimeProposal]


class RuntimePlanUpdateDecision(BaseModel):
    should_update: bool
    next_plan: str | None = None

    @model_validator(mode="after")
    def validate_next_plan(self) -> "RuntimePlanUpdateDecision":
        if self.should_update and self.next_plan is None:
            raise ValueError("next_plan is required when should_update is true")
        return self


def test_sync_step_execution_sets_execution_reference_step_execution_id() -> None:
    observed_step_execution_ids: list[str | None] = []

    @dataclass
    class StepIdAssertingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_step_kinds = request.allowed_step_kinds
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds
            observed_step_execution_ids.append(nh.get_execution_reference().step_execution_id)
            assert nh.get_execution_reference().step_execution_id == step_context.execution_reference.step_execution_id
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 11})

    with nh.run(StepIdAssertingExecutor()):

        @nh.natural_function
        def f(x: int):
            """natural
            <x>
            <:result>
            This is a docstring Natural block.
            """
            _ = x
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert nh.get_execution_reference().step_execution_id is None
        assert f(10) == 11
        assert nh.get_execution_reference().step_execution_id is None

    assert observed_step_execution_ids and observed_step_execution_ids[0] is not None


def test_natural_function_updates_output_binding_via_docstring_step():
    nh.StepExecutorConfiguration()

    @dataclass
    class AssertingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_step_kinds = request.allowed_step_kinds
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds

            assert step_context.step_locals["x"] == 10
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 11})

    with nh.run(AssertingExecutor()):

        @nh.natural_function
        def f(x: int):
            """natural
            <x>
            <:result>
            This is a docstring Natural block.
            """
            _ = x
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f(10) == 11


def test_async_natural_function_updates_output_binding_via_docstring_step():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        @nh.natural_function
        async def f(x: int) -> int:
            """natural
            <x>
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 11}}
            """
            _ = x
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(f(10)) == 11


def test_pass_step_finalize_coerces_committed_model_binding() -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> str:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": {"child": {"value": "7"}}}}
            """
            result: RuntimeResultModel
            assert result.child is not None  # noqa: F821  # pyright: ignore[reportUndefinedVariable, reportUnboundVariable, reportAttributeAccessIssue]
            return f"{type(result).__name__}:{type(result.child).__name__}:{result.child.value}"  # noqa: F821  # pyright: ignore[reportUndefinedVariable, reportUnboundVariable, reportAttributeAccessIssue]

        assert f() == "RuntimeResultModel:RuntimeChildModel:7"


def test_pass_step_finalize_coerces_discriminated_union_list_items() -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> str:
            """natural
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": {"proposals": [{"kind": "approve", "score": "5"}]}}}
            """
            result: RuntimeProposalEnvelope
            first_proposal = result.proposals[0]  # noqa: F821  # pyright: ignore[reportUndefinedVariable, reportUnboundVariable, reportAttributeAccessIssue]
            return f"{type(first_proposal).__name__}:{first_proposal.score}"

        assert f() == "RuntimeApproveProposal:5"


def test_pass_step_finalize_rejects_cross_field_model_violation() -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            <:decision>
            {"step_outcome": {"kind": "pass"}, "bindings": {"decision": {"should_update": true, "next_plan": null}}}
            """
            decision: RuntimePlanUpdateDecision
            return 1 if decision.should_update else 0  # noqa: F821  # pyright: ignore[reportUndefinedVariable, reportUnboundVariable, reportAttributeAccessIssue]

        with pytest.raises(ExecutionError, match="Output binding 'decision' failed validation"):
            f()


def test_async_step_execution_sets_execution_reference_step_execution_id() -> None:
    observed_step_execution_ids: list[str | None] = []

    @dataclass
    class AsyncStepIdAssertingExecutor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_step_kinds = request.allowed_step_kinds
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds
            observed_step_execution_ids.append(nh.get_execution_reference().step_execution_id)
            assert nh.get_execution_reference().step_execution_id == step_context.execution_reference.step_execution_id
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 13})

    with nh.run(AsyncStepIdAssertingExecutor()):

        @nh.natural_function
        async def f() -> int:
            """natural
            <:result>
            execute one step.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert nh.get_execution_reference().step_execution_id is None
        assert asyncio.run(f()) == 13
        assert nh.get_execution_reference().step_execution_id is None

    assert observed_step_execution_ids and observed_step_execution_ids[0] is not None


def test_async_natural_function_awaits_awaitable_return_value_from_step_executor():
    nh.StepExecutorConfiguration()

    @dataclass
    class AssertingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_step_kinds = request.allowed_step_kinds
            _ = processed_natural_program
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            raise AssertionError("run_step should not be used for this async test")

        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_step_kinds = request.allowed_step_kinds
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds
            _ = step_context

            async def calculate() -> int:
                return 17

            step_context.record_assignment("calculate", calculate)
            return await resolve_model_candidate(request, ReturnStepOutcome(kind="return", return_expression="calculate()"), {})

    with nh.run(AssertingExecutor()):

        @nh.natural_function
        async def f() -> int:
            """natural
            return the value.
            """
            return 0

        assert asyncio.run(f()) == 17


def test_sync_natural_function_rejects_awaitable_return_value_from_step_executor():
    nh.StepExecutorConfiguration()

    class AwaitableInt:
        def __await__(self):  # type: ignore[no-untyped-def]
            if False:
                yield None
            return 17

    @dataclass
    class AssertingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_step_kinds = request.allowed_step_kinds
            _ = processed_natural_program
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            step_context.record_assignment("result", AwaitableInt())
            return run_coroutine_synchronously(
                lambda: resolve_model_candidate(request, ReturnStepOutcome(kind="return", return_expression="result"), {})
            )

    with nh.run(AssertingExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            return the value.
            """
            return 0

        with pytest.raises(NighthawkError, match="awaitable"):
            f()


def test_stub_return_effect_returns_value_from_return_expression():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 11}}
            """
            result = 0
            return result

        assert f() == 11


def test_stub_return_effect_invalid_return_value_raises():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": "not an int"}}
            """
            result = 0
            return result

        with pytest.raises(ExecutionError):
            f()


def test_stub_return_effect_invalid_return_expression_raises():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            {"step_outcome": {"kind": "return", "return_expression": "missing"}, "bindings": {}}
            """
            return 0

        with pytest.raises(ExecutionError):
            f()


def test_stub_continue_effect_skips_following_statements():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            total = 0
            for _ in range(5):
                total += 1
                """natural
                {"step_outcome": {"kind": "continue"}, "bindings": {}}
                """
                total += 100
            return total

        assert f() == 5


def test_stub_break_effect_breaks_loop():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            total = 0
            for _ in range(5):
                total += 1
                """natural
                {"step_outcome": {"kind": "break"}, "bindings": {}}
                """
                total += 100
            return total

        assert f() == 1


def test_stub_break_outside_loop_raises():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            {"step_outcome": {"kind": "break"}, "bindings": {}}
            """
            return 1

        with pytest.raises(ExecutionError):
            f()


def test_docstring_step_is_literal_no_implicit_interpolation():
    nh.StepExecutorConfiguration()

    @dataclass
    class RecordingExecutor:
        seen_programs: list[str] = field(default_factory=list)

        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_step_kinds = request.allowed_step_kinds
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            self.seen_programs.append(processed_natural_program)
            return StepCandidate(outcome=Pass(), binding_name_to_value={})

    recording_executor = RecordingExecutor()

    with nh.run(recording_executor):

        @nh.natural_function
        def f() -> None:
            """natural
            This should remain literal: {GLOBAL_NUMBER}
            """

        f()

    assert recording_executor.seen_programs == ["This should remain literal: {GLOBAL_NUMBER}\n"]


def test_frontmatter_deny_return_rejects_return_step():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            ---
            deny:
              - return
            ---
            {"step_outcome": {"kind": "return", "return_expression": "result"}, "bindings": {"result": 0}}
            """
            return 0

        with pytest.raises(ExecutionError, match="not allowed"):
            f()


def test_frontmatter_deny_return_recognizes_leading_blank_lines():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            result = 0
            """natural

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

        @nh.natural_function
        def f(x: int):
            computed_result = x + 1
            envelope_json_text = json.dumps(
                {
                    "step_outcome": {"kind": "pass"},
                    "bindings": {"result": computed_result},
                }
            )

            f"""natural
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


def test_natural_function_rejects_step_executor_configuration_updates_for_non_agent_step_executor() -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> None:
            with nh.scope(step_executor_configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna")):
                pass

        with pytest.raises(NighthawkError, match="ConfigurableStepExecutor"):
            f()


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

        @nh.natural_function
        def f() -> None:
            """natural
            Execute one step.
            """

        f()

    assert "search_repository" in observed_task_reference_name_set
    assert "SHADOWED_NUMBER" not in observed_task_reference_name_set


def test_unannotated_binding_type_inferred_from_initial_value():
    """binding_name_to_type should reflect the inferred type for unannotated write bindings."""
    from nighthawk.testing import ScriptedExecutor, pass_response

    executor = ScriptedExecutor(responses=[pass_response(result="hello")])
    with nh.run(executor):

        @nh.natural_function
        def f() -> str:
            result = ""
            """natural
            <:result>
            This is a docstring Natural block.
            """

            return result

        assert f() == "hello"

    assert executor.calls[0].binding_name_to_type["result"] is str
