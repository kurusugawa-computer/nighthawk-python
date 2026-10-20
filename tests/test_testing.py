from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.runtime import Pass, StepDefinition, StepResult
from nighthawk.testing import (
    CallbackExecutor,
    ScriptedExecutor,
    StepCall,
    StepResponse,
    break_response,
    continue_response,
    pass_response,
    raise_response,
    return_response,
)


def _module_level_helper(query: str) -> list[str]:
    """Fetch items matching the query."""
    return [f"item_{query}"]


class ReviewVerdict(BaseModel):
    approved: bool
    reason: str
    risk_level: str


class TestScriptedExecutorPassOutcome:
    def test_returns_binding_value(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"result": "hello"})])

        @nh.function
        def greet(name: str) -> str:
            result: str = ""
            """execute
            Read <name> and set <:result> to a greeting.
            """
            return result

        with nh.run(executor):
            output = greet("world")

        assert output == "hello"

    def test_records_call_metadata(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"result": ""})])

        @nh.function
        def process(query: str) -> str:
            result: str = ""
            """execute
            Use <_module_level_helper> to process <query> and set <:result>.
            """
            return result

        with nh.run(executor):
            process("test")

        assert len(executor.calls) == 1
        call = executor.calls[0]
        assert "query" in call.step_locals
        assert call.step_locals["query"] == "test"
        assert "result" in call.binding_names
        assert "_module_level_helper" in call.step_globals
        assert call.step_globals["_module_level_helper"] is _module_level_helper

    def test_filters_globals_to_referenced_names_only(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"result": ""})])

        @nh.function
        def minimal(text: str) -> str:
            result: str = ""
            """execute
            Summarize <text> and set <:result>.
            """
            return result

        with nh.run(executor):
            minimal("hello")

        call = executor.calls[0]
        assert "__builtins__" not in call.step_globals
        assert "__name__" not in call.step_globals

    def test_default_response_used_when_responses_exhausted(self) -> None:
        executor = ScriptedExecutor(
            [pass_response(binding_name_to_value={"result": "first"})], default_response=pass_response(binding_name_to_value={"result": "default"})
        )

        @nh.function
        def f() -> str:
            result: str = ""
            """execute
            Set <:result>.
            """
            return result

        with nh.run(executor):
            assert f() == "first"
            assert f() == "default"
            assert f() == "default"

        assert len(executor.calls) == 3

    def test_multiple_responses_consumed_in_order(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                pass_response(binding_name_to_value={"result": "first"}),
                pass_response(binding_name_to_value={"result": "second"}),
            ]
        )

        @nh.function
        def f() -> str:
            result: str = ""
            """execute
            Set <:result>.
            """
            return result

        with nh.run(executor):
            assert f() == "first"
            assert f() == "second"


@pytest.mark.asyncio
@pytest.mark.parametrize("response_count", [0, 1])
async def test_unexpected_invocation_fails_without_committing_outputs(execute: Callable[..., Awaitable[StepResult]], response_count: int) -> None:
    executor = ScriptedExecutor([pass_response(binding_name_to_value={"value": 7}) for _ in range(response_count)])
    definition = StepDefinition("Set <:value>.", output_binding_names=("value",), binding_name_to_type={"value": int})
    writes: list[tuple[str, object]] = []

    def write_binding(name: str, value: object) -> None:
        writes.append((name, value))

    with nh.run(executor):
        for _ in range(response_count):
            outcome = await execute(definition, name_to_value={}, write_binding=write_binding)
            assert isinstance(outcome, Pass)
        with pytest.raises(nh.ExecutionError, match=f"no response for invocation {response_count + 1}") as caught:
            await execute(definition, name_to_value={}, write_binding=write_binding)

    assert isinstance(caught.value.__cause__, AssertionError)
    assert caught.value.step_failed is not None
    assert caught.value.step_failed.failure_stage == "executor"
    assert len(executor.calls) == response_count + 1
    assert writes == [("value", 7)] * response_count


class TestScriptedExecutorRaiseOutcome:
    def test_raises_exception_by_default(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                raise_response("something went wrong"),
            ]
        )

        @nh.function
        def f() -> str:
            result: str = ""
            """execute
            Set <:result>.
            """
            return result

        with nh.run(executor), pytest.raises(Exception, match="something went wrong") as caught:
            f()
        assert type(caught.value) is Exception

    def test_raises_custom_error_type(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                raise_response("bad input", error_type=ValueError),
            ]
        )

        @nh.function
        def f(data: str) -> str:
            result: str = ""
            """execute
            <ValueError>
            Validate <data> and set <:result>.
            """
            return result

        with nh.run(executor), pytest.raises(ValueError, match="bad input"):
            f("???")


class TestScriptedExecutorBreakOutcome:
    def test_breaks_loop(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                pass_response(),
                pass_response(),
                break_response(),
            ]
        )

        @nh.function
        def f() -> int:
            count = 0
            for _i in range(10):
                count += 1
                """execute
                Decide whether to continue.
                """
            return count

        with nh.run(executor):
            assert f() == 3


class TestScriptedExecutorContinueOutcome:
    def test_continues_loop(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                continue_response(),
                pass_response(),
                continue_response(),
            ]
        )

        @nh.function
        def f() -> list[int]:
            results: list[int] = []
            for i in range(3):
                """execute
                Decide whether to process this item.
                """
                results.append(i)
            return results

        with nh.run(executor):
            result = f()

        assert result == [1]


class TestScriptedExecutorReturnOutcome:
    def test_early_return(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                return_response("early", binding_name_to_value={"result": "early"}),
            ]
        )

        @nh.function
        def f() -> str:
            result: str = ""
            """execute
            Set <:result>.
            """
            result = "late"
            return result

        with nh.run(executor):
            assert f() == "early"


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("value", ["effects.append('evaluated')", None, object()])
async def test_resolved_return_preserves_values_without_expression_effects(callback: bool, asynchronous: bool, value: object) -> None:
    effects: list[str] = []
    response = return_response(value, binding_name_to_value={"value": "independent output"})
    executor = CallbackExecutor(lambda call: response) if callback else ScriptedExecutor([response])

    @nh.function
    def synchronous() -> object:
        nonlocal effects
        """execute
        Read <effects> and set <:value>.
        Return a result.
        """
        raise AssertionError("Expected an early return")

    @nh.function
    async def asynchronous_function() -> object:
        nonlocal effects
        """execute
        Read <effects> and set <:value>.
        Return a result.
        """
        raise AssertionError("Expected an early return")

    with nh.run(executor):
        result = await asynchronous_function() if asynchronous else synchronous()
    assert result is value
    assert effects == []
    assert len(executor.calls) == 1


class TestScriptedExecutorAllowedStepKinds:
    def test_records_allowed_step_kinds_in_loop(self) -> None:
        executor = ScriptedExecutor(responses=[break_response()])

        @nh.function
        def f() -> None:
            for _i in range(1):
                """execute
                Do something.
                """

        with nh.run(executor):
            f()

        call = executor.calls[0]
        assert "break" in call.allowed_step_kinds
        assert "continue" in call.allowed_step_kinds

    def test_records_allowed_step_kinds_outside_loop(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response()])

        @nh.function
        def f() -> None:
            """execute
            Do something.
            """

        with nh.run(executor):
            f()

        call = executor.calls[0]
        assert "break" not in call.allowed_step_kinds
        assert "continue" not in call.allowed_step_kinds


class TestScriptedExecutorBindingFiltering:
    def test_rejects_extra_bindings_not_in_declared_outputs(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                pass_response(binding_name_to_value={"result": "hello", "extra": "invalid"}),
            ]
        )

        @nh.function
        def f() -> str:
            result: str = ""
            """execute
            Set <:result>.
            """
            return result

        with nh.run(executor), pytest.raises(nh.ExecutionError, match="Undeclared output.*extra"):
            f()


class TestCallbackExecutor:
    def test_delegates_to_handler(self) -> None:
        def handler(call: StepCall) -> StepResponse:
            text = call.step_locals.get("text", "")
            if isinstance(text, str) and "urgent" in text:
                return pass_response(binding_name_to_value={"category": "high"})
            return pass_response(binding_name_to_value={"category": "normal"})

        executor = CallbackExecutor(handler)

        @nh.function
        def classify(text: str) -> str:
            category: str = ""
            """execute
            Classify <text> and set <:category>.
            """
            return category

        with nh.run(executor):
            assert classify("server down") == "normal"
            assert classify("urgent outage") == "high"

    def test_handler_receives_step_locals(self) -> None:
        received_locals: dict[str, object] = {}

        def handler(call: StepCall) -> StepResponse:
            received_locals.update(call.step_locals)
            return pass_response(binding_name_to_value={"result": "ok"})

        executor = CallbackExecutor(handler)

        @nh.function
        def f(data: str) -> str:
            result: str = ""
            """execute
            Process <data> and set <:result>.
            """
            return result

        with nh.run(executor):
            f("input_value")

        assert received_locals["data"] == "input_value"

    def test_records_calls(self) -> None:
        executor = CallbackExecutor(lambda _: pass_response(binding_name_to_value={"result": ""}))

        @nh.function
        def f() -> str:
            result: str = ""
            """execute
            Set <:result>.
            """
            return result

        with nh.run(executor):
            f()
            f()

        assert len(executor.calls) == 2


class TestConvenienceFactories:
    def test_pass_response_creates_pass_outcome(self) -> None:
        response = pass_response(binding_name_to_value={"x": 1, "y": "two"})
        assert response.outcome.kind == "pass"
        assert response.binding_name_to_value == {"x": 1, "y": "two"}

    def test_raise_response_creates_raise_outcome(self) -> None:
        response = raise_response("boom", error_type=ValueError)
        assert response.outcome.kind == "raise"
        assert response.outcome.message == "boom"  # type: ignore[union-attr]
        assert response.outcome.error_type is ValueError  # type: ignore[union-attr]

    def test_raise_response_without_error_type(self) -> None:
        response = raise_response("fail")
        assert response.outcome.error_type is Exception  # type: ignore[union-attr]

    def test_return_response_creates_return_outcome(self) -> None:
        response = return_response(42, binding_name_to_value={"result": 42})
        assert response.outcome.kind == "return"
        assert response.outcome.value == 42  # type: ignore[union-attr]
        assert response.binding_name_to_value == {"result": 42}

    def test_break_response_creates_break_outcome(self) -> None:
        response = break_response()
        assert response.outcome.kind == "break"
        assert response.binding_name_to_value is nh.UNSET

    def test_continue_response_creates_continue_outcome(self) -> None:
        response = continue_response()
        assert response.outcome.kind == "continue"
        assert response.binding_name_to_value is nh.UNSET


class TestCarryPatternMock:
    def test_carry_visible_in_step_locals_across_steps(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                pass_response(binding_name_to_value={"result": 10}),
                pass_response(binding_name_to_value={"result": 20}),
            ]
        )

        @nh.function
        def step_1(carry: list[str]) -> int:
            result = 0
            """execute
            Set <:result> to 10. Append a summary to <carry>.
            """
            return result

        @nh.function
        def step_2(carry: list[str]) -> int:
            result = 0
            """execute
            Read <carry> and set <:result>.
            """
            return result

        carry: list[str] = ["seed"]
        with nh.run(executor):
            r1 = step_1(carry)
            r2 = step_2(carry)

        assert r1 == 10
        assert r2 == 20
        assert executor.calls[0].step_locals["carry"] == ["seed"]
        assert executor.calls[1].step_locals["carry"] == ["seed"]

    def test_carry_branching_produces_independent_copies(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                pass_response(binding_name_to_value={"result": 105}),
                pass_response(binding_name_to_value={"result": 200}),
            ]
        )

        @nh.function
        def branch_fn(carry: list[str]) -> int:
            result = 0
            """execute
            Read <carry> and set <:result>.
            """
            return result

        carry: list[str] = ["original"]
        carry_a = carry.copy()
        carry_b = carry.copy()
        carry_a.append("branch_a_extra")

        with nh.run(executor):
            result_a = branch_fn(carry_a)
            result_b = branch_fn(carry_b)

        assert result_a == 105
        assert result_b == 200
        assert executor.calls[0].step_locals["carry"] == ["original", "branch_a_extra"]
        assert executor.calls[1].step_locals["carry"] == ["original"]

    def test_carry_mutation_via_callback(self) -> None:
        def handler(call: StepCall) -> StepResponse:
            carry = call.step_locals.get("carry")
            if isinstance(carry, list):
                carry.append(f"step_{len(carry) + 1}")
            return pass_response(binding_name_to_value={"result": (len(carry) if isinstance(carry, list) else 0) * 10})

        executor = CallbackExecutor(handler)

        @nh.function
        def step(carry: list[str]) -> int:
            result = 0
            """execute
            Set <:result>. Append to <carry>.
            """
            return result

        carry: list[str] = []
        with nh.run(executor):
            r1 = step(carry)
            r2 = step(carry)

        assert r1 == 10
        assert r2 == 20
        assert carry == ["step_1", "step_2"]


class TestPydanticReturnTypeCoercion:
    def test_dict_coerced_to_pydantic_model_on_return(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                return_response({"approved": False, "reason": "uses eval()", "risk_level": "high"}),
            ]
        )

        @nh.function
        def judge_review(review_data: str) -> ReviewVerdict:
            """execute
            Analyze <review_data> and return a structured verdict.
            """
            raise AssertionError("The scripted response returns early")

        with nh.run(executor):
            result = judge_review("code uses eval()")

        assert isinstance(result, ReviewVerdict)
        assert not result.approved
        assert result.risk_level == "high"


class TestFStringNaturalBlock:
    def test_fstring_injection_resolved_before_executor(self) -> None:
        received_programs: list[str] = []

        def handler(call: StepCall) -> StepResponse:
            received_programs.append(call.natural_program)
            return pass_response(binding_name_to_value={"result": "ok"})

        executor = CallbackExecutor(handler)

        @nh.function
        def process(context_text: str) -> str:
            result: str = ""
            f"""execute
            Prior context: {context_text}
            Set <:result> based on the context.
            """
            return result

        with nh.run(executor):
            process("important note")

        assert len(received_programs) == 1
        assert "important note" in received_programs[0]
        assert "{context_text}" not in received_programs[0]
