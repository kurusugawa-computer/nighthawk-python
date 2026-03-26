"""Tests for nighthawk.testing utilities."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

import nighthawk as nh
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

# ── Helpers ──


def _module_level_helper(query: str) -> list[str]:
    """Fetch items matching the query."""
    return [f"item_{query}"]


class ReviewVerdict(BaseModel):
    approved: bool
    reason: str
    risk_level: str


# ── ScriptedExecutor tests ──


class TestScriptedExecutorPassOutcome:
    def test_returns_binding_value(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(result="hello")])

        @nh.natural_function
        def greet(name: str) -> str:
            result: str = ""
            """natural
            Read <name> and set <:result> to a greeting.
            """
            return result

        with nh.run(executor):
            output = greet("world")

        assert output == "hello"

    def test_records_call_metadata(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response(result="")])

        @nh.natural_function
        def process(query: str) -> str:
            result: str = ""
            """natural
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
        executor = ScriptedExecutor(responses=[pass_response(result="")])

        @nh.natural_function
        def minimal(text: str) -> str:
            result: str = ""
            """natural
            Summarize <text> and set <:result>.
            """
            return result

        with nh.run(executor):
            minimal("hello")

        call = executor.calls[0]
        assert "__builtins__" not in call.step_globals
        assert "__name__" not in call.step_globals

    def test_default_response_used_when_responses_exhausted(self) -> None:
        executor = ScriptedExecutor(default_response=pass_response(result="default"))

        @nh.natural_function
        def f() -> str:
            result: str = ""
            """natural
            Set <:result>.
            """
            return result

        with nh.run(executor):
            assert f() == "default"
            assert f() == "default"

        assert len(executor.calls) == 2

    def test_multiple_responses_consumed_in_order(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                pass_response(result="first"),
                pass_response(result="second"),
            ]
        )

        @nh.natural_function
        def f() -> str:
            result: str = ""
            """natural
            Set <:result>.
            """
            return result

        with nh.run(executor):
            assert f() == "first"
            assert f() == "second"


class TestScriptedExecutorRaiseOutcome:
    def test_raises_execution_error(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                raise_response("something went wrong"),
            ]
        )

        @nh.natural_function
        def f() -> str:
            result: str = ""
            """natural
            Set <:result>.
            """
            return result

        with nh.run(executor), pytest.raises(nh.ExecutionError, match="something went wrong"):
            f()

    def test_raises_custom_error_type(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                raise_response("bad input", error_type="ValueError"),
            ]
        )

        @nh.natural_function
        def f(data: str) -> str:
            result: str = ""
            """natural
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

        @nh.natural_function
        def f() -> int:
            count = 0
            for _i in range(10):
                count += 1
                """natural
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

        @nh.natural_function
        def f() -> list[int]:
            results: list[int] = []
            for i in range(3):
                """natural
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
                return_response("result", result="early"),
            ]
        )

        @nh.natural_function
        def f() -> str:
            result: str = ""
            """natural
            Set <:result>.
            """
            result = "late"
            return result

        with nh.run(executor):
            assert f() == "early"


class TestScriptedExecutorAllowedStepKinds:
    def test_records_allowed_step_kinds_in_loop(self) -> None:
        executor = ScriptedExecutor(responses=[break_response()])

        @nh.natural_function
        def f() -> None:
            for _i in range(1):
                """natural
                Do something.
                """

        with nh.run(executor):
            f()

        call = executor.calls[0]
        assert "break" in call.allowed_step_kinds
        assert "continue" in call.allowed_step_kinds

    def test_records_allowed_step_kinds_outside_loop(self) -> None:
        executor = ScriptedExecutor(responses=[pass_response()])

        @nh.natural_function
        def f() -> None:
            """natural
            Do something.
            """

        with nh.run(executor):
            f()

        call = executor.calls[0]
        assert "break" not in call.allowed_step_kinds
        assert "continue" not in call.allowed_step_kinds


class TestScriptedExecutorBindingFiltering:
    def test_ignores_extra_bindings_not_in_binding_names(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                pass_response(result="hello", extra="ignored"),
            ]
        )

        @nh.natural_function
        def f() -> str:
            result: str = ""
            """natural
            Set <:result>.
            """
            return result

        with nh.run(executor):
            output = f()

        assert output == "hello"


# ── CallbackExecutor tests ──


class TestCallbackExecutor:
    def test_delegates_to_handler(self) -> None:
        def handler(call: StepCall) -> StepResponse:
            text = call.step_locals.get("text", "")
            if isinstance(text, str) and "urgent" in text:
                return pass_response(category="high")
            return pass_response(category="normal")

        executor = CallbackExecutor(handler)

        @nh.natural_function
        def classify(text: str) -> str:
            category: str = ""
            """natural
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
            return pass_response(result="ok")

        executor = CallbackExecutor(handler)

        @nh.natural_function
        def f(data: str) -> str:
            result: str = ""
            """natural
            Process <data> and set <:result>.
            """
            return result

        with nh.run(executor):
            f("input_value")

        assert received_locals["data"] == "input_value"

    def test_records_calls(self) -> None:
        executor = CallbackExecutor(lambda _: pass_response(result=""))

        @nh.natural_function
        def f() -> str:
            result: str = ""
            """natural
            Set <:result>.
            """
            return result

        with nh.run(executor):
            f()
            f()

        assert len(executor.calls) == 2


# ── Convenience factory tests ──


class TestConvenienceFactories:
    def test_pass_response_creates_pass_outcome(self) -> None:
        response = pass_response(x=1, y="two")
        assert response.outcome.kind == "pass"
        assert response.bindings == {"x": 1, "y": "two"}

    def test_raise_response_creates_raise_outcome(self) -> None:
        response = raise_response("boom", error_type="ValueError")
        assert response.outcome.kind == "raise"
        assert response.outcome.raise_message == "boom"  # type: ignore[union-attr]
        assert response.outcome.raise_error_type == "ValueError"  # type: ignore[union-attr]

    def test_raise_response_without_error_type(self) -> None:
        response = raise_response("fail")
        assert response.outcome.raise_error_type is None  # type: ignore[union-attr]

    def test_return_response_creates_return_outcome(self) -> None:
        response = return_response("result", result=42)
        assert response.outcome.kind == "return"
        assert response.outcome.return_expression == "result"  # type: ignore[union-attr]
        assert response.bindings == {"result": 42}

    def test_break_response_creates_break_outcome(self) -> None:
        response = break_response()
        assert response.outcome.kind == "break"
        assert response.bindings == {}

    def test_continue_response_creates_continue_outcome(self) -> None:
        response = continue_response()
        assert response.outcome.kind == "continue"
        assert response.bindings == {}


# ── Carry pattern mock tests (P-TEST-002) ──


class TestCarryPatternMock:
    def test_carry_visible_in_step_locals_across_steps(self) -> None:
        """Carry list appears in step_locals for consecutive Natural block calls."""
        executor = ScriptedExecutor(
            responses=[
                pass_response(result=10),
                pass_response(result=20),
            ]
        )

        @nh.natural_function
        def step_1(carry: list[str]) -> int:
            result = 0
            """natural
            Set <:result> to 10. Append a summary to <carry>.
            """
            return result

        @nh.natural_function
        def step_2(carry: list[str]) -> int:
            result = 0
            """natural
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
        """copy() on carry creates independent continuations visible in separate calls."""
        executor = ScriptedExecutor(
            responses=[
                pass_response(result=105),
                pass_response(result=200),
            ]
        )

        @nh.natural_function
        def branch_fn(carry: list[str]) -> int:
            result = 0
            """natural
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
        """CallbackExecutor can simulate in-place carry mutation."""

        def handler(call: StepCall) -> StepResponse:
            carry = call.step_locals.get("carry")
            if isinstance(carry, list):
                carry.append(f"step_{len(carry) + 1}")
            return pass_response(result=(len(carry) if isinstance(carry, list) else 0) * 10)

        executor = CallbackExecutor(handler)

        @nh.natural_function
        def step(carry: list[str]) -> int:
            result = 0
            """natural
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


# ── Pydantic write binding mock tests (P-TEST-003) ──


class TestPydanticReturnTypeCoercion:
    def test_dict_coerced_to_pydantic_model_on_return(self) -> None:
        """A dict returned via return_response is coerced to the Pydantic return type."""
        executor = ScriptedExecutor(
            responses=[
                return_response("verdict", verdict={"approved": False, "reason": "uses eval()", "risk_level": "high"}),
            ]
        )

        @nh.natural_function
        def judge_review(review_data: str) -> ReviewVerdict:
            verdict: ReviewVerdict
            """natural
            Analyze <review_data> and produce a structured <:verdict>.
            """
            return verdict  # noqa: F821  # pyright: ignore[reportUnboundVariable]

        with nh.run(executor):
            result = judge_review("code uses eval()")

        assert isinstance(result, ReviewVerdict)
        assert not result.approved
        assert result.risk_level == "high"


# ── f-string Natural block mock tests (P-TEST-006) ──


class TestFStringNaturalBlock:
    def test_fstring_injection_resolved_before_executor(self) -> None:
        """f-string expressions are evaluated before the Natural program reaches the executor."""
        received_programs: list[str] = []

        def handler(call: StepCall) -> StepResponse:
            received_programs.append(call.natural_program)
            return pass_response(result="ok")

        executor = CallbackExecutor(handler)

        @nh.natural_function
        def process(context_text: str) -> str:
            result: str = ""
            f"""natural
            Prior context: {context_text}
            Set <:result> based on the context.
            """
            return result

        with nh.run(executor):
            process("important note")

        assert len(received_programs) == 1
        assert "important note" in received_programs[0]
        assert "{context_text}" not in received_programs[0]
