from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.runtime import Break, Continue, Pass, Raise, Return, StepCandidate, StepDefinition, StepOutcome, StepRequest
from nighthawk.testing import CallbackExecutor, ScriptedExecutor


def _module_level_helper(query: str) -> list[str]:
    """Fetch items matching the query."""
    return [f"item_{query}"]


class ReviewVerdict(BaseModel):
    approved: bool
    reason: str
    risk_level: str


class TestScriptedExecutorPassOutcome:
    def test_returns_binding_value(self) -> None:
        executor = ScriptedExecutor([StepCandidate(binding_name_to_value={"result": "hello"})])

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
        executor = ScriptedExecutor([StepCandidate(binding_name_to_value={"result": ""})])

        @nh.function
        def process(query: str) -> str:
            result: str = ""
            """execute
            Use <_module_level_helper> to process <query> and set <:result>.
            """
            return result

        with nh.run(executor):
            process("test")

        assert len(executor.requests) == 1
        request = executor.requests[0]
        environment = request.step_context.environment
        assert environment.resolve("query") == "test"
        assert request.output_binding_names == ("result",)
        assert environment.resolve("_module_level_helper") is _module_level_helper

    def test_default_response_used_when_responses_exhausted(self) -> None:
        executor = ScriptedExecutor(
            [StepCandidate(binding_name_to_value={"result": "first"})], default_response=StepCandidate(binding_name_to_value={"result": "default"})
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

        assert len(executor.requests) == 3

    def test_multiple_responses_consumed_in_order(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                StepCandidate(binding_name_to_value={"result": "first"}),
                StepCandidate(binding_name_to_value={"result": "second"}),
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
async def test_unexpected_invocation_fails_without_committing_outputs(execute: Callable[..., Awaitable[StepOutcome]], response_count: int) -> None:
    executor = ScriptedExecutor([StepCandidate(binding_name_to_value={"value": 7}) for _ in range(response_count)])
    definition = StepDefinition("Set <:value>.", output_binding_names=("value",), binding_name_to_type={"value": int})
    writes: list[tuple[str, object]] = []

    def write_binding(name: str, value: object) -> None:
        writes.append((name, value))

    with nh.run(executor):
        for _ in range(response_count):
            outcome = await execute(definition, name_to_value={}, write_binding=write_binding)
            assert isinstance(outcome, Pass)
        with pytest.raises(nh.ExecutionError, match=f"no response for request {response_count + 1}") as caught:
            await execute(definition, name_to_value={}, write_binding=write_binding)

    assert isinstance(caught.value.__cause__, AssertionError)
    assert caught.value.step_failed.failure_stage == "executor"
    assert len(executor.requests) == response_count + 1
    assert writes == [("value", 7)] * response_count


class TestScriptedExecutorRaiseOutcome:
    def test_raises_exception_by_default(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                StepCandidate(outcome=Raise("something went wrong")),
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
                StepCandidate(outcome=Raise("bad input", ValueError)),
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
                StepCandidate(),
                StepCandidate(),
                StepCandidate(outcome=Break()),
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
                StepCandidate(outcome=Continue()),
                StepCandidate(),
                StepCandidate(outcome=Continue()),
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
                StepCandidate(outcome=Return("early"), binding_name_to_value={"result": "early"}),
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
    response = StepCandidate(outcome=Return(value), binding_name_to_value={"value": "independent output"})
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
    assert len(executor.requests) == 1


class TestScriptedExecutorAllowedOutcomeKinds:
    def test_records_allowed_outcome_kinds_in_loop(self) -> None:
        executor = ScriptedExecutor([StepCandidate(outcome=Break())])

        @nh.function
        def f() -> None:
            for _i in range(1):
                """execute
                Do something.
                """

        with nh.run(executor):
            f()

        call = executor.requests[0]
        assert "break" in call.allowed_outcome_kinds
        assert "continue" in call.allowed_outcome_kinds

    def test_records_allowed_outcome_kinds_outside_loop(self) -> None:
        executor = ScriptedExecutor([StepCandidate()])

        @nh.function
        def f() -> None:
            """execute
            Do something.
            """

        with nh.run(executor):
            f()

        call = executor.requests[0]
        assert "break" not in call.allowed_outcome_kinds
        assert "continue" not in call.allowed_outcome_kinds


class TestScriptedExecutorBindingFiltering:
    def test_rejects_extra_bindings_not_in_declared_outputs(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                StepCandidate(binding_name_to_value={"result": "hello", "extra": "invalid"}),
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
        def handler(request: StepRequest) -> StepCandidate:
            text = request.step_context.environment.resolve("text")
            if isinstance(text, str) and "urgent" in text:
                return StepCandidate(binding_name_to_value={"category": "high"})
            return StepCandidate(binding_name_to_value={"category": "normal"})

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

    def test_handler_receives_live_environment(self) -> None:
        received_values: list[object] = []

        def handler(request: StepRequest) -> StepCandidate:
            received_values.append(request.step_context.environment.resolve("data"))
            return StepCandidate(binding_name_to_value={"result": "ok"})

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

        assert received_values == ["input_value"]

    def test_records_requests(self) -> None:
        executor = CallbackExecutor(lambda _: StepCandidate(binding_name_to_value={"result": ""}))

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

        assert len(executor.requests) == 2


class TestStepCandidateDefaults:
    def test_defaults_to_pass_with_automatic_output_selection(self) -> None:
        candidate = StepCandidate()
        assert candidate.outcome == Pass()
        assert candidate.binding_name_to_value is nh.UNSET


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_kind", ["synchronous", "asynchronous", "returns_awaitable"])
async def test_callback_completes_once_in_the_active_scope(execute: Callable[..., Awaitable[StepOutcome]], handler_kind: str) -> None:
    value = object()
    completed: list[StepRequest] = []
    writes: list[tuple[str, object]] = []
    caller_thread = threading.get_ident()

    def respond(request: StepRequest) -> StepCandidate:
        assert nh.get_step_context() is request.step_context
        assert nh.get_scope().task_references["value"] is value
        completed.append(request)
        return StepCandidate(binding_name_to_value={"result": value})

    def synchronous(request: StepRequest) -> StepCandidate:
        assert threading.get_ident() == caller_thread
        return respond(request)

    async def asynchronous(request: StepRequest) -> StepCandidate:
        await asyncio.sleep(0)
        return respond(request)

    def returns_awaitable(request: StepRequest) -> Awaitable[StepCandidate]:
        assert threading.get_ident() == caller_thread
        return asynchronous(request)

    handler = synchronous if handler_kind == "synchronous" else asynchronous if handler_kind == "asynchronous" else returns_awaitable
    executor = CallbackExecutor(handler)

    def write_binding(name: str, selected: object) -> None:
        assert completed == executor.requests
        writes.append((name, selected))

    with nh.run(executor), nh.scope(task_references={"value": value}):
        outcome = await execute(
            StepDefinition("Choose a result.", output_binding_names=("result",), binding_name_to_type={"result": object}),
            name_to_value={},
            write_binding=write_binding,
        )

    assert isinstance(outcome, Pass)
    assert len(completed) == len(executor.requests) == 1
    assert writes == [("result", value)]


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_callback_failure_finishes_before_terminal_delivery(execute: Callable[..., Awaitable[StepOutcome]], interrupted: bool) -> None:
    failure = asyncio.CancelledError("interrupted callback") if interrupted else ValueError("failed callback")
    effects: list[str] = []
    events: list[nh.lifecycle.StepFinished] = []
    writes: list[tuple[str, object]] = []

    async def handler(request: StepRequest) -> StepCandidate:
        try:
            await asyncio.sleep(0)
            raise failure
        finally:
            effects.append("finished")

    def record_finished(event: nh.lifecycle.StepFinished) -> None:
        assert effects == ["finished"]
        events.append(event)

    def write_binding(name: str, value: object) -> None:
        writes.append((name, value))

    executor = CallbackExecutor(handler)
    with (
        nh.run(executor),
        nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=record_finished)),
        pytest.raises(asyncio.CancelledError if interrupted else nh.ExecutionError) as caught,
    ):
        await execute(
            StepDefinition("Choose a result.", output_binding_names=("result",)),
            name_to_value={"result": "initial"},
            write_binding=write_binding,
        )

    assert len(executor.requests) == len(events) == 1
    event = events[0]
    assert isinstance(event, nh.lifecycle.StepInterrupted if interrupted else nh.lifecycle.StepFailed)
    assert event.original_exception is failure
    assert event.failure_stage == "executor"
    assert (caught.value if interrupted else caught.value.__cause__) is failure
    assert writes == []


class TestCarryPatternMock:
    def test_carry_visible_in_environment_across_steps(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                StepCandidate(binding_name_to_value={"result": 10}),
                StepCandidate(binding_name_to_value={"result": 20}),
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
        assert executor.requests[0].step_context.environment.resolve("carry") == ["seed"]
        assert executor.requests[1].step_context.environment.resolve("carry") == ["seed"]

    def test_carry_branching_produces_independent_copies(self) -> None:
        executor = ScriptedExecutor(
            responses=[
                StepCandidate(binding_name_to_value={"result": 105}),
                StepCandidate(binding_name_to_value={"result": 200}),
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
        assert executor.requests[0].step_context.environment.resolve("carry") == ["original", "branch_a_extra"]
        assert executor.requests[1].step_context.environment.resolve("carry") == ["original"]

    def test_carry_mutation_via_callback(self) -> None:
        def handler(request: StepRequest) -> StepCandidate:
            carry = request.step_context.environment.resolve("carry")
            if isinstance(carry, list):
                carry.append(f"step_{len(carry) + 1}")
            return StepCandidate(binding_name_to_value={"result": (len(carry) if isinstance(carry, list) else 0) * 10})

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
                StepCandidate(outcome=Return({"approved": False, "reason": "uses eval()", "risk_level": "high"})),
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

        def handler(request: StepRequest) -> StepCandidate:
            received_programs.append(request.processed_natural_program)
            return StepCandidate(binding_name_to_value={"result": "ok"})

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


def test_scripted_decision_reports_block_kind_and_explicit_evidence() -> None:
    @nh.function
    def supported(document: str) -> bool:
        result: bool = False
        """decide <:result>
        Does <document> support the claim?
        """
        return result

    executor = ScriptedExecutor([StepCandidate(binding_name_to_value={"result": True})])
    with nh.run(executor):
        assert supported("evidence")
    assert executor.requests[0].block_kind == "decide"
    assert executor.requests[0].input_binding_name_to_value == {"document": "evidence"}
