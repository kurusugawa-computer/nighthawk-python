import asyncio
from dataclasses import dataclass, field
from typing import Annotated, Literal

import pytest
from pydantic import AfterValidator, BaseModel, Field, model_validator

import nighthawk as nh
from nighthawk.errors import ExecutionError
from nighthawk.runtime import Pass, StepCandidate, StepRequest
from nighthawk.testing import ScriptedExecutor, break_response, continue_response, pass_response, return_response

GLOBAL_NUMBER = 7


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
            allowed_outcome_kinds = request.allowed_outcome_kinds
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_outcome_kinds
            observed_step_execution_ids.append(nh.get_execution_reference().step_execution_id)
            assert nh.get_execution_reference().step_execution_id == step_context.execution_reference.step_execution_id
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 11})

    with nh.run(StepIdAssertingExecutor()):

        @nh.function
        def f(x: int):
            """execute
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
    @dataclass
    class AssertingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_outcome_kinds = request.allowed_outcome_kinds
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_outcome_kinds

            assert step_context.step_locals["x"] == 10
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 11})

    with nh.run(AssertingExecutor()):

        @nh.function
        def f(x: int):
            """execute
            <x>
            <:result>
            This is a docstring Natural block.
            """
            _ = x
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f(10) == 11


def test_async_natural_function_updates_output_binding_via_docstring_step():
    with nh.run(ScriptedExecutor(default_response=pass_response(binding_name_to_value={"result": 11}))):

        @nh.function
        async def f(x: int) -> int:
            """execute
            <x>
            <:result>
            Complete this step.
            """
            _ = x
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(f(10)) == 11


def test_pass_step_finalize_coerces_committed_model_binding() -> None:
    with nh.run(ScriptedExecutor(default_response=pass_response(binding_name_to_value={"result": {"child": {"value": "7"}}}))):

        @nh.function
        def f() -> str:
            """execute
            <:result>
            Complete this step.
            """
            result: RuntimeResultModel
            assert result.child is not None  # noqa: F821  # pyright: ignore[reportUndefinedVariable, reportUnboundVariable, reportAttributeAccessIssue]
            return f"{type(result).__name__}:{type(result.child).__name__}:{result.child.value}"  # noqa: F821  # pyright: ignore[reportUndefinedVariable, reportUnboundVariable, reportAttributeAccessIssue]

        assert f() == "RuntimeResultModel:RuntimeChildModel:7"


def test_pass_step_finalize_coerces_discriminated_union_list_items() -> None:
    with nh.run(
        ScriptedExecutor(default_response=pass_response(binding_name_to_value={"result": {"proposals": [{"kind": "approve", "score": "5"}]}}))
    ):

        @nh.function
        def f() -> str:
            """execute
            <:result>
            Complete this step.
            """
            result: RuntimeProposalEnvelope
            first_proposal = result.proposals[0]  # noqa: F821  # pyright: ignore[reportUndefinedVariable, reportUnboundVariable, reportAttributeAccessIssue]
            return f"{type(first_proposal).__name__}:{first_proposal.score}"

        assert f() == "RuntimeApproveProposal:5"


def test_pass_step_finalize_rejects_cross_field_model_violation() -> None:
    with nh.run(ScriptedExecutor(default_response=pass_response(binding_name_to_value={"decision": {"should_update": True, "next_plan": None}}))):

        @nh.function
        def f() -> int:
            """execute
            <:decision>
            Complete this step.
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
            allowed_outcome_kinds = request.allowed_outcome_kinds
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_outcome_kinds
            observed_step_execution_ids.append(nh.get_execution_reference().step_execution_id)
            assert nh.get_execution_reference().step_execution_id == step_context.execution_reference.step_execution_id
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 13})

    with nh.run(AsyncStepIdAssertingExecutor()):

        @nh.function
        async def f() -> int:
            """execute
            <:result>
            execute one step.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert nh.get_execution_reference().step_execution_id is None
        assert asyncio.run(f()) == 13
        assert nh.get_execution_reference().step_execution_id is None

    assert observed_step_execution_ids and observed_step_execution_ids[0] is not None


def test_resolved_return_value_is_validated() -> None:
    @nh.function
    def workflow() -> int:
        """execute
        Return an integer.
        """
        return 0

    with nh.run(ScriptedExecutor([return_response("not an int")])), pytest.raises(ExecutionError):
        workflow()


def test_resolved_continue_effect_skips_following_statements():
    with nh.run(ScriptedExecutor(default_response=continue_response())):

        @nh.function
        def f() -> int:
            total = 0
            for _ in range(5):
                total += 1
                """execute
                Complete this step.
                """
                total += 100
            return total

        assert f() == 5


def test_resolved_break_effect_breaks_loop():
    with nh.run(ScriptedExecutor(default_response=break_response())):

        @nh.function
        def f() -> int:
            total = 0
            for _ in range(5):
                total += 1
                """execute
                Complete this step.
                """
                total += 100
            return total

        assert f() == 1


def test_resolved_break_outside_loop_raises():
    with nh.run(ScriptedExecutor(default_response=break_response())):

        @nh.function
        def f() -> int:
            """execute
            Complete this step.
            """
            return 1

        with pytest.raises(ExecutionError):
            f()


def test_docstring_step_is_literal_no_implicit_interpolation():
    @dataclass
    class RecordingExecutor:
        seen_programs: list[str] = field(default_factory=list)

        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            binding_names = list(request.output_binding_names)
            allowed_outcome_kinds = request.allowed_outcome_kinds
            _ = step_context
            _ = binding_names
            _ = allowed_outcome_kinds
            self.seen_programs.append(processed_natural_program)
            return StepCandidate(outcome=Pass(), binding_name_to_value={})

    recording_executor = RecordingExecutor()

    with nh.run(recording_executor):

        @nh.function
        def f() -> None:
            """execute
            This should remain literal: {GLOBAL_NUMBER}
            """

        f()

    assert recording_executor.seen_programs == ["This should remain literal: {GLOBAL_NUMBER}\n"]


def test_compound_annotation_rejects_out_of_range_value() -> None:
    @nh.function
    def workflow() -> int:
        if True:
            result: Annotated[int, Field(ge=0, le=10)] = 0
        """execute
        <:result>
        Complete this step.
        """
        return result

    with (
        nh.run(ScriptedExecutor(default_response=pass_response(binding_name_to_value={"result": 11}))),
        pytest.raises(nh.ExecutionError, match="less than or equal to 10"),
    ):
        workflow()


def test_initialized_output_is_validated_without_executor_assignment() -> None:
    @nh.function
    def workflow() -> str:
        result: Annotated[str, AfterValidator(str.strip)] = " draft "
        """execute
        <:result>
        Complete this step.
        """
        return result

    with nh.run(ScriptedExecutor(default_response=pass_response())):
        assert workflow() == "draft"


def test_invalid_initialized_output_fails_without_executor_assignment() -> None:
    @nh.function
    def workflow() -> int:
        result: Annotated[int, Field(ge=0)] = -1
        """execute
        <:result>
        Complete this step.
        """
        return result

    with nh.run(ScriptedExecutor(default_response=pass_response())), pytest.raises(nh.ExecutionError, match="greater than or equal to 0"):
        workflow()
