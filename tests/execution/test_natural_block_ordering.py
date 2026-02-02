from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.execution.context import ExecutionContext
from nighthawk.execution.llm import ExecutionFinal


class RuntimeMemory(BaseModel):
    pass


NATURAL_BLOCK_ORDERING_GLOBAL_NUMBER = 7


def create_workspace_directories(workspace_root: Path) -> None:
    (workspace_root / "docs").mkdir()
    (workspace_root / "tests").mkdir()


def test_docstring_block_executes_first_and_template_name_is_undefined(tmp_path: Path) -> None:
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    @dataclass
    class NoopExecutor:
        def run_natural_block(
            self,
            *,
            processed_natural_program: str,
            execution_context: ExecutionContext,
            binding_names: list[str],
            is_in_loop: bool,
            allowed_effect_types: tuple[str, ...] = ("return", "break", "continue"),
        ) -> tuple[ExecutionFinal, dict[str, object]]:
            _ = processed_natural_program
            _ = execution_context
            _ = binding_names
            _ = is_in_loop
            _ = allowed_effect_types
            return ExecutionFinal(effect=None, error=None), {}

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=NoopExecutor(),
            memory=RuntimeMemory(),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            <:result>
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {later_value}}}}}
            """
            later_value = 123
            _ = later_value
            result = 0
            return result

        with pytest.raises(UnboundLocalError, match="later_value"):
            f()


def test_inline_blocks_execute_in_place_and_observe_updated_locals(tmp_path: Path) -> None:
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    class RecordingExecutor:
        def __init__(self) -> None:
            self.seen_programs: list[str] = []

        def run_natural_block(
            self,
            *,
            processed_natural_program: str,
            execution_context: "ExecutionContext",
            binding_names: list[str],
            is_in_loop: bool,
            allowed_effect_types: tuple[str, ...] = ("return", "break", "continue"),
        ) -> tuple[ExecutionFinal, dict[str, object]]:
            from nighthawk.execution.llm import ExecutionFinal

            _ = execution_context
            _ = binding_names
            _ = is_in_loop
            _ = allowed_effect_types

            self.seen_programs.append(processed_natural_program)
            return ExecutionFinal(effect=None, error=None), {}

    recording_executor = RecordingExecutor()

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=recording_executor,
            memory=RuntimeMemory(),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            x = 1
            """natural
            First: {x}
            """
            x = 2
            _ = x
            """natural
            Second: {x}
            """

        f()

    assert recording_executor.seen_programs == ["First: 1\n", "Second: 2\n"]


def test_missing_input_binding_raises_even_if_template_does_not_reference_it(tmp_path: Path) -> None:
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    @dataclass
    class NoopExecutor:
        def run_natural_block(
            self,
            *,
            processed_natural_program: str,
            execution_context: "ExecutionContext",
            binding_names: list[str],
            is_in_loop: bool,
            allowed_effect_types: tuple[str, ...] = ("return", "break", "continue"),
        ) -> tuple[ExecutionFinal, dict[str, object]]:
            from nighthawk.execution.llm import ExecutionFinal

            _ = processed_natural_program
            _ = execution_context
            _ = binding_names
            _ = is_in_loop
            _ = allowed_effect_types
            return ExecutionFinal(effect=None, error=None), {}

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=NoopExecutor(),
            memory=RuntimeMemory(),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            """natural
            <missing>
            Hello.
            """

        with pytest.raises(NameError, match="missing"):
            f()


def test_input_binding_globals_are_injected_into_execution_locals_for_agent_tool_eval(tmp_path: Path) -> None:
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    GLOBAL_NUMBER = NATURAL_BLOCK_ORDERING_GLOBAL_NUMBER

    class FakeRunResult:
        def __init__(self, output: object):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):
            from nighthawk.execution.llm import ExecutionFinal
            from nighthawk.tools import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            assign_result = assign_tool(deps, "result", "NATURAL_BLOCK_ORDERING_GLOBAL_NUMBER")
            assert assign_result["ok"] is True

            return FakeRunResult(ExecutionFinal(effect=None, error=None))

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=FakeAgent()),
            memory=RuntimeMemory(),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            <NATURAL_BLOCK_ORDERING_GLOBAL_NUMBER>
            <:result>
            Set result.
            """
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f() == GLOBAL_NUMBER


def test_agent_backend_commits_only_on_assignment(tmp_path: Path) -> None:
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output: object):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt: str, *, deps=None, **kwargs):
            from nighthawk.execution.llm import ExecutionFinal

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            return FakeRunResult(ExecutionFinal(effect=None, error=None))

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=FakeAgent()),
            memory=RuntimeMemory(),
            workspace_root=tmp_path,
        )
    ):
        from nighthawk.execution.orchestrator import Orchestrator

        orchestrator = Orchestrator.from_environment(nh.get_environment())
        frame = nh.execution.orchestrator.get_caller_frame()  # type: ignore[attr-defined]

        envelope = orchestrator.run_natural_block(
            "Hello.",
            input_binding_names=[],
            output_binding_names=["result"],
            binding_name_to_type={},
            return_annotation=int,
            is_in_loop=False,
            caller_frame=frame,
        )

    assert envelope["bindings"] == {}
