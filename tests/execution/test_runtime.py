import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import nighthawk as nh
from nighthawk.errors import ExecutionError, NighthawkError
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_contract import PassStepOutcome, ReturnStepOutcome
from tests.execution.stub_executor import StubExecutor

GLOBAL_NUMBER = 7
SHADOWED_NUMBER = 1


def global_import_file(file_path: Path | str) -> str:
    _ = file_path
    return '{"step_outcome": {"kind": "pass"}, "bindings": {"result": 20}}'


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
            allowed_step_kinds: tuple[str, ...],
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
            allowed_step_kinds: tuple[str, ...],
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
            allowed_step_kinds: tuple[str, ...],
        ) -> tuple[ReturnStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = binding_names
            _ = allowed_step_kinds
            _ = step_context

            async def calculate() -> int:
                return 17

            return ReturnStepOutcome(kind="return", return_reference_path="result"), {"result": calculate()}

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
            allowed_step_kinds: tuple[str, ...],
        ) -> tuple[ReturnStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            return ReturnStepOutcome(kind="return", return_reference_path="result"), {"result": AwaitableInt()}

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


def test_stub_return_effect_returns_value_from_return_reference_path():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            <:result>
            {"step_outcome": {"kind": "return", "return_reference_path": "result"}, "bindings": {"result": 11}}
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
            {"step_outcome": {"kind": "return", "return_reference_path": "result"}, "bindings": {"result": "not an int"}}
            """
            result = 0
            return result

        with pytest.raises(ExecutionError):
            f()


def test_stub_return_effect_invalid_return_reference_path_raises():
    nh.StepExecutorConfiguration()
    with nh.run(StubExecutor()):

        @nh.natural_function
        def f() -> int:
            """natural
            {"step_outcome": {"kind": "return", "return_reference_path": "missing"}, "bindings": {}}
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
            allowed_step_kinds: tuple[str, ...],
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
            {"step_outcome": {"kind": "return", "return_reference_path": "result"}, "bindings": {"result": 0}}
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
            {"step_outcome": {"kind": "return", "return_reference_path": "result"}, "bindings": {"result": 11}}
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


def test_natural_function_can_override_step_executor_configuration_model_within_scope() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class RecordingAgent:
        def __init__(self) -> None:
            self.seen_model_identifiers: list[str] = []

        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.runtime.step_contract import PassStepOutcome
            from nighthawk.tools.assignment import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            current_step_executor = nh.get_step_executor()
            assert isinstance(current_step_executor, nh.AgentStepExecutor)
            current_model_identifier = current_step_executor.configuration.model
            self.seen_model_identifiers.append(current_model_identifier)
            assign_tool(deps, "observed_model_identifier", repr(current_model_identifier))

            return FakeRunResult(PassStepOutcome(kind="pass"))

    initial_model_identifier = "openai-responses:gpt-5-nano"
    overridden_model_identifier = "openai-responses:gpt-5-mini"
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

            with nh.scope(
                step_executor_configuration_patch=nh.StepExecutorConfigurationPatch(
                    model="openai-responses:gpt-5-mini"
                )
            ):
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
            with nh.scope(
                step_executor_configuration_patch=nh.StepExecutorConfigurationPatch(
                    model="openai-responses:gpt-5-mini"
                )
            ):
                pass

        with pytest.raises(NighthawkError, match="AgentStepExecutor"):
            f()
