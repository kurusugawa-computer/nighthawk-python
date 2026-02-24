from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_contract import PassStepOutcome


class RuntimeMemory(BaseModel):
    pass


NATURAL_BLOCK_ORDERING_GLOBAL_NUMBER = 7


def test_docstring_step_executes_first_and_name_is_undefined() -> None:
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    @dataclass
    class NoopExecutor:
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[str, ...],
        ) -> tuple[PassStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            return PassStepOutcome(kind="pass"), {}

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=NoopExecutor(),
        )
    ):

        @nh.natural_function
        def f() -> int:
            """natural
            <later_value>
            <:result>
            {"step_outcome": {"kind": "pass"}, "bindings": {"result": 0}}
            """
            later_value = 123
            _ = later_value
            result = 0
            return result

        with pytest.raises(UnboundLocalError, match="later_value"):
            f()


def test_missing_input_binding_raises_even_if_program_text_does_not_use_it() -> None:
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    @dataclass
    class NoopExecutor:
        def run_step(
            self,
            *,
            processed_natural_program: str,
            step_context: StepContext,
            binding_names: list[str],
            allowed_step_kinds: tuple[str, ...],
        ) -> tuple[PassStepOutcome, dict[str, object]]:
            _ = processed_natural_program
            _ = step_context
            _ = binding_names
            _ = allowed_step_kinds
            return PassStepOutcome(kind="pass"), {}

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=NoopExecutor(),
        )
    ):

        @nh.natural_function
        def f() -> None:
            """natural
            <missing>
            Hello.
            """

        with pytest.raises(NameError, match="missing"):
            f()


def test_input_binding_globals_are_injected_into_step_locals_for_agent_tool_eval() -> None:
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    GLOBAL_NUMBER = NATURAL_BLOCK_ORDERING_GLOBAL_NUMBER

    class FakeRunResult:
        def __init__(self, output: object):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):
            from nighthawk.runtime.step_contract import PassStepOutcome
            from nighthawk.tools.assignment import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            assign_tool(deps, "result", "NATURAL_BLOCK_ORDERING_GLOBAL_NUMBER")

            return FakeRunResult(PassStepOutcome(kind="pass"))

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=nh.AgentStepExecutor(agent=FakeAgent()),
        )
    ):

        @nh.natural_function
        def f() -> int:
            """natural
            <NATURAL_BLOCK_ORDERING_GLOBAL_NUMBER>
            <:result>
            Set result.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f() == GLOBAL_NUMBER


def test_agent_backend_commits_only_on_assignment() -> None:
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output: object):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):
            from nighthawk.runtime.step_contract import PassStepOutcome

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            return FakeRunResult(PassStepOutcome(kind="pass"))

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=nh.AgentStepExecutor(agent=FakeAgent()),
        )
    ):
        from nighthawk.runtime.runner import Runner

        runner = Runner.from_environment(nh.get_environment())
        frame = nh.runtime.runner.get_caller_frame()  # type: ignore[attr-defined]

        envelope = runner.run_step(
            "Hello.",
            input_binding_names=[],
            output_binding_names=["result"],
            binding_name_to_type={},
            return_annotation=int,
            is_in_loop=False,
            caller_frame=frame,
        )

    assert envelope["bindings"] == {}
