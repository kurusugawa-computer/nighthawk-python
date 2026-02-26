import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import nighthawk as nh
from nighthawk.errors import ExecutionError
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_contract import PassStepOutcome, ReturnStepOutcome
from tests.execution.stub_executor import StubExecutor

GLOBAL_NUMBER = 7
SHADOWED_NUMBER = 1


def global_import_file(file_path: Path | str) -> str:
    _ = file_path
    return '{"step_outcome": {"kind": "pass"}, "bindings": {"result": 20}}'


def test_natural_function_updates_output_binding_via_docstring_step():
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

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

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=AssertingExecutor(),
        )
    ):

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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

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

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=AssertingExecutor(),
        )
    ):

        @nh.natural_function
        async def f() -> int:
            """natural
            return the value.
            """
            return 0

        assert asyncio.run(f()) == 17


def test_sync_natural_function_rejects_awaitable_return_value_from_step_executor():
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

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

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=AssertingExecutor(),
        )
    ):

        @nh.natural_function
        def f() -> int:
            """natural
            return the value.
            """
            return 0

        with pytest.raises(ExecutionError, match="awaitable"):
            f()


def test_async_natural_function_allows_self_reference_freevar():
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

        @nh.natural_function
        async def f() -> int:
            """natural
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
            """
            if False:
                return await f()
            return 17

        assert asyncio.run(f()) == 17


def test_stub_return_effect_returns_value_from_return_reference_path():
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

        @nh.natural_function
        def f() -> int:
            """natural
            {"step_outcome": {"kind": "return", "return_reference_path": "missing"}, "bindings": {}}
            """
            return 0

        with pytest.raises(ExecutionError):
            f()


def test_stub_continue_effect_skips_following_statements():
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

        @nh.natural_function
        def f() -> int:
            """natural
            {"step_outcome": {"kind": "break"}, "bindings": {}}
            """
            return 1

        with pytest.raises(ExecutionError):
            f()


def test_docstring_step_is_literal_no_implicit_interpolation():
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

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

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=recording_executor,
        )
    ):

        @nh.natural_function
        def f() -> None:
            """natural
            This should remain literal: {GLOBAL_NUMBER}
            """

        f()

    assert recording_executor.seen_programs == ["This should remain literal: {GLOBAL_NUMBER}\n"]


def test_frontmatter_deny_return_rejects_return_step():
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )
    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=StubExecutor(),
        )
    ):

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
