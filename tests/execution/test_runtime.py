import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, model_validator
from pydantic_ai.messages import BinaryContent

import nighthawk as nh
from nighthawk.errors import ExecutionError, NighthawkError
from nighthawk.runtime.prompt import build_system_prompt, resolve_step_system_prompt_template_text
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_contract import PassStepOutcome, ReturnStepOutcome, StepFinalResult, StepKind
from tests.execution.stub_executor import StubExecutor

GLOBAL_NUMBER = 7
SHADOWED_NUMBER = 1


class RuntimeChildModel(BaseModel):
    value: int


class RuntimeResultModel(BaseModel):
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


def global_import_file(file_path: Path | str) -> str:
    _ = file_path
    return '{"step_outcome": {"kind": "pass"}, "bindings": {"result": 20}}'


def test_sync_step_execution_sets_execution_ref_step_id() -> None:
    observed_step_ids: list[str | None] = []

    @dataclass
    class StepIdAssertingExecutor:
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[PassStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds
            observed_step_ids.append(nh.get_execution_ref().step_id)
            assert nh.get_execution_ref().step_id == step_context.step_id
            return PassStepOutcome(kind="pass"), {"result": 11}

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

        assert nh.get_execution_ref().step_id is None
        assert f(10) == 11
        assert nh.get_execution_ref().step_id is None

    assert observed_step_ids and observed_step_ids[0] is not None


def test_natural_function_updates_output_binding_via_docstring_step():
    nh.StepExecutorConfiguration()

    @dataclass
    class AssertingExecutor:
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[PassStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds

            assert step_context.step_locals["x"] == 10
            return PassStepOutcome(kind="pass"), {"result": 11}

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


def test_async_step_execution_sets_execution_ref_step_id() -> None:
    observed_step_ids: list[str | None] = []

    @dataclass
    class AsyncStepIdAssertingExecutor:
        async def run_step_async(
            self,
            *,
            processed_natural_program: str,
            step_context: StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[PassStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds
            observed_step_ids.append(nh.get_execution_ref().step_id)
            assert nh.get_execution_ref().step_id == step_context.step_id
            return PassStepOutcome(kind="pass"), {"result": 13}

    with nh.run(AsyncStepIdAssertingExecutor()):

        @nh.natural_function
        async def f() -> int:
            """natural
            <:result>
            execute one step.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert nh.get_execution_ref().step_id is None
        assert asyncio.run(f()) == 13
        assert nh.get_execution_ref().step_id is None

    assert observed_step_ids and observed_step_ids[0] is not None


def test_async_natural_function_awaits_awaitable_return_value_from_step_executor():
    nh.StepExecutorConfiguration()

    @dataclass
    class AssertingExecutor:
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[ReturnStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            raise AssertionError("run_step should not be used for this async test")

        async def run_step_async(
            self,
            *,
            processed_natural_program: str,
            step_context: StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[ReturnStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds
            _ = step_context

            async def calculate() -> int:
                return 17

            return ReturnStepOutcome(kind="return", return_expression="result"), {"result": calculate()}

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
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[ReturnStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            return ReturnStepOutcome(kind="return", return_expression="result"), {"result": AwaitableInt()}

    with nh.run(AssertingExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            return the value.
            """
            return 0

        with pytest.raises(ExecutionError, match="awaitable"):
            f()


def test_async_natural_function_allows_self_reference_freevar():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        @nh.natural_function
        async def f() -> int:
            """natural
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
            """
            if False:
                return await f()
            return 17

        assert asyncio.run(f()) == 17


def test_natural_function_supports_instance_method():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalMethodCarrier:
            @nh.natural_function
            def evaluate(self, value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 31}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert NaturalMethodCarrier().evaluate(10) == 31


def test_natural_function_supports_staticmethod_for_both_decorator_orders():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalStaticMethodCarrier:
            @staticmethod
            @nh.natural_function
            def static_inner(value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 41}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            @nh.natural_function
            @staticmethod
            def static_outer(value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 42}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert NaturalStaticMethodCarrier.static_inner(10) == 41
        assert NaturalStaticMethodCarrier.static_outer(10) == 42


def test_natural_function_supports_classmethod_for_both_decorator_orders():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalClassMethodCarrier:
            @classmethod
            @nh.natural_function
            def class_inner(cls, value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 51}}
                """
                _ = cls
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            @nh.natural_function
            @classmethod
            def class_outer(cls, value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 52}}
                """
                _ = cls
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert NaturalClassMethodCarrier.class_inner(10) == 51
        assert NaturalClassMethodCarrier.class_outer(10) == 52


def test_natural_function_supports_async_instance_method():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalAsyncMethodCarrier:
            @nh.natural_function
            async def evaluate(self, value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 61}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(NaturalAsyncMethodCarrier().evaluate(10)) == 61


def test_natural_function_supports_async_staticmethod_for_both_decorator_orders():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalAsyncStaticMethodCarrier:
            @staticmethod
            @nh.natural_function
            async def static_inner(value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 71}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            @nh.natural_function
            @staticmethod
            async def static_outer(value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 72}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(NaturalAsyncStaticMethodCarrier.static_inner(10)) == 71
        assert asyncio.run(NaturalAsyncStaticMethodCarrier.static_outer(10)) == 72


def test_natural_function_supports_async_classmethod_for_both_decorator_orders():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalAsyncClassMethodCarrier:
            @classmethod
            @nh.natural_function
            async def class_inner(cls, value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 81}}
                """
                _ = cls
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            @nh.natural_function
            @classmethod
            async def class_outer(cls, value: int) -> int:
                """natural
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 82}}
                """
                _ = cls
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(NaturalAsyncClassMethodCarrier.class_inner(10)) == 81
        assert asyncio.run(NaturalAsyncClassMethodCarrier.class_outer(10)) == 82


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

        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: object,
            binding_names: list[str],
            allowed_step_kinds: tuple[StepKind, ...],
        ) -> tuple[PassStepOutcome, dict[str, object]]:
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            self.seen_programs.append(processed_natural_program)
            return PassStepOutcome(kind="pass"), {}

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


def test_step_system_prompt_omits_preview_budget_by_default() -> None:
    configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_tokens=8_000,
            locals_max_items=80,
            globals_max_tokens=4_000,
            globals_max_items=40,
            value_max_tokens=200,
            object_max_methods=16,
            object_max_fields=16,
            object_field_value_max_tokens=120,
            tool_result_max_tokens=4_321,
        )
    )

    resolved_system_prompt_text = build_system_prompt(configuration=configuration)
    assert "max 4321 tokens" not in resolved_system_prompt_text
    assert "$tool_result_max_tokens" not in resolved_system_prompt_text


def test_system_prompt_suffix_fragment_injects_tool_result_max_tokens() -> None:
    configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_tokens=8_000,
            locals_max_items=80,
            globals_max_tokens=4_000,
            globals_max_items=40,
            value_max_tokens=200,
            object_max_methods=16,
            object_max_fields=16,
            object_field_value_max_tokens=120,
            tool_result_max_tokens=4_321,
        )
    )

    resolved_fragment_text = resolve_step_system_prompt_template_text(
        template_text="Preview budget: max $tool_result_max_tokens tokens.",
        tool_result_max_tokens=configuration.context_limits.tool_result_max_tokens,
    )

    assert resolved_fragment_text == "Preview budget: max 4321 tokens."


def test_agent_executor_commits_write_binding_after_dotted_assignment() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.tools.assignment import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            assign_tool(deps, "result.value", "9")
            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    with nh.run(nh.AgentStepExecutor.from_agent(agent=FakeAgent())):

        @nh.natural_function
        def f() -> int:
            result: RuntimeResultModel = RuntimeResultModel(value=0)
            """natural
            <:result>
            Update the result value.
            """
            return result.value

        assert f() == 9


def test_agent_executor_passes_plain_string_to_text_only_custom_agent() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class RecordingAgent:
        def __init__(self) -> None:
            self.seen_prompt_type_list: list[type[object]] = []

        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            assert deps is not None
            _ = kwargs
            self.seen_prompt_type_list.append(type(user_prompt))
            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    recording_agent = RecordingAgent()

    with nh.run(nh.AgentStepExecutor.from_agent(agent=recording_agent)):

        @nh.natural_function
        def f() -> None:
            """natural
            Say hi.
            """

        f()

    assert recording_agent.seen_prompt_type_list == [str]


def test_agent_executor_passes_multimodal_tuple_to_custom_agent() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class RecordingAgent:
        def __init__(self) -> None:
            self.seen_prompt_list: list[object] = []

        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            assert deps is not None
            _ = kwargs
            self.seen_prompt_list.append(user_prompt)
            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    recording_agent = RecordingAgent()
    image = BinaryContent(data=b"\x89PNG\r\n\x1a\n", media_type="image/png", identifier="img")

    with nh.run(nh.AgentStepExecutor.from_agent(agent=recording_agent)):

        @nh.natural_function
        def f(photo: BinaryContent) -> None:
            """natural
            Inspect <photo>.
            """

        f(image)

    assert len(recording_agent.seen_prompt_list) == 1
    seen_prompt = recording_agent.seen_prompt_list[0]
    assert isinstance(seen_prompt, tuple)
    assert any(isinstance(content, BinaryContent) for content in seen_prompt)


def test_natural_function_can_override_step_executor_configuration_model_within_scope() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class RecordingAgent:
        def __init__(self) -> None:
            self.seen_model_identifiers: list[str] = []

        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.runtime.step_contract import PassStepOutcome, StepFinalResult
            from nighthawk.tools.assignment import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            current_step_executor = nh.get_step_executor()
            assert isinstance(current_step_executor, nh.AgentStepExecutor)
            current_model_identifier = current_step_executor.configuration.model
            self.seen_model_identifiers.append(current_model_identifier)
            assign_tool(deps, "observed_model_identifier", repr(current_model_identifier))

            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    initial_model_identifier = "openai-responses:gpt-5.4-nano"
    overridden_model_identifier = "openai-responses:gpt-5.4-mini"
    recording_agent = RecordingAgent()
    step_executor_configuration = nh.StepExecutorConfiguration(model=initial_model_identifier)
    step_executor = nh.AgentStepExecutor.from_agent(
        agent=recording_agent,
        configuration=step_executor_configuration,
    )

    with nh.run(step_executor):

        @nh.natural_function
        def f() -> tuple[str, str, str]:
            """natural
            <:observed_model_identifier>
            Record the current model identifier.
            """
            first_model_identifier = observed_model_identifier  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            with nh.scope(step_executor_configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-mini")):
                """natural
                <:observed_model_identifier>
                Record the current model identifier.
                """
                second_model_identifier = observed_model_identifier  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            """natural
            <:observed_model_identifier>
            Record the current model identifier.
            """
            third_model_identifier = observed_model_identifier  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            return (
                first_model_identifier,
                second_model_identifier,
                third_model_identifier,
            )

        assert f() == (
            initial_model_identifier,
            overridden_model_identifier,
            initial_model_identifier,
        )

    assert recording_agent.seen_model_identifiers == [
        initial_model_identifier,
        overridden_model_identifier,
        initial_model_identifier,
    ]


def test_natural_function_rejects_step_executor_configuration_updates_for_non_agent_step_executor() -> None:
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> None:
            with nh.scope(step_executor_configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-mini")):
                pass

        with pytest.raises(NighthawkError, match="AgentStepExecutor"):
            f()


def test_scope_implicit_references_are_merged_into_step_context() -> None:
    from nighthawk.testing import CallbackExecutor, pass_response

    observed_implicit_reference_name_set: set[str] = set()

    def search_repository(query: str) -> list[str]:
        _ = query
        return ["hit"]

    def handler(call):  # type: ignore[no-untyped-def]
        observed_implicit_reference_name_set.update(call.step_globals.keys())
        return pass_response()

    executor = CallbackExecutor(handler)

    with nh.run(executor), nh.scope(implicit_references={"search_repository": search_repository}):

        @nh.natural_function
        def f() -> None:
            """natural
            Execute one step.
            """

        f()

    assert "search_repository" in observed_implicit_reference_name_set
    assert "SHADOWED_NUMBER" not in observed_implicit_reference_name_set


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
