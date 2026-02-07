import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.errors import ExecutionError
from tests.execution.stub_executor import StubExecutor


class RuntimeMemory(BaseModel):
    pass


GLOBAL_NUMBER = 7
SHADOWED_NUMBER = 1


def global_import_file(file_path: Path | str) -> str:
    _ = file_path
    return '{"execution_final": {"effect": null, "error": null}, "bindings": {"result": 20}}'


def create_workspace_directories(workspace_root: Path) -> None:
    (workspace_root / "docs").mkdir()
    (workspace_root / "tests").mkdir()


def test_fn_updates_output_binding_via_docstring_natural_block(tmp_path: Path):
    create_workspace_directories(tmp_path)

    from nighthawk.execution.context import ExecutionContext
    from nighthawk.execution.contracts import ExecutionFinal

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    @dataclass
    class AssertingExecutor:
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
            _ = binding_names
            _ = is_in_loop
            _ = allowed_effect_types

            assert execution_context.execution_locals["x"] == 10
            return ExecutionFinal(effect=None, error=None), {"result": 11}

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=AssertingExecutor(),
            memory=RuntimeMemory(),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f(x: int):
            """natural
            <x>
            <:result>
            This is a docstring Natural block.
            """
            _ = x
            return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert f(10) == 11


def test_stub_return_effect_returns_value_from_source_path(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            <:result>
            {"execution_final": {"effect": {"type": "return", "source_path": "result"}, "error": null}, "bindings": {"result": 11}}
            """
            result = 0
            return result

        assert f() == 11


def test_stub_return_effect_invalid_return_value_raises(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            <:result>
            {"execution_final": {"effect": {"type": "return", "source_path": "result"}, "error": null}, "bindings": {"result": "not an int"}}
            """
            result = 0
            return result

        with pytest.raises(ExecutionError):
            f()


def test_stub_return_effect_invalid_source_path_raises(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            {"execution_final": {"effect": {"type": "return", "source_path": "missing"}, "error": null}, "bindings": {}}
            """
            return 0

        with pytest.raises(ExecutionError):
            f()


def test_stub_continue_effect_skips_following_statements(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            total = 0
            for _ in range(5):
                total += 1
                """natural
                {"execution_final": {"effect": {"type": "continue", "source_path": null}, "error": null}, "bindings": {}}
                """
                total += 100
            return total

        assert f() == 5


def test_stub_break_effect_breaks_loop(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            total = 0
            for _ in range(5):
                total += 1
                """natural
                {"execution_final": {"effect": {"type": "break", "source_path": null}, "error": null}, "bindings": {}}
                """
                total += 100
            return total

        assert f() == 1


def test_stub_break_outside_loop_raises(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            {"execution_final": {"effect": {"type": "break", "source_path": null}, "error": null}, "bindings": {}}
            """
            return 1

        with pytest.raises(ExecutionError):
            f()


def test_docstring_natural_block_is_literal_no_implicit_interpolation(tmp_path: Path):
    create_workspace_directories(tmp_path)

    from nighthawk.execution.contracts import ExecutionFinal

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    @dataclass
    class RecordingExecutor:
        seen_programs: list[str] = field(default_factory=list)

        def run_natural_block(
            self,
            *,
            processed_natural_program: str,
            execution_context: object,
            binding_names: list[str],
            is_in_loop: bool,
            allowed_effect_types: tuple[str, ...] = ("return", "break", "continue"),
        ) -> tuple[ExecutionFinal, dict[str, object]]:
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
            """natural
            This should remain literal: {GLOBAL_NUMBER}
            """

        f()

    assert recording_executor.seen_programs == ["This should remain literal: {GLOBAL_NUMBER}\n"]


def test_frontmatter_deny_return_rejects_return_effect(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            """natural
            ---
            deny:
              - return
            ---
            {"execution_final": {"effect": {"type": "return", "source_path": null}, "error": null}, "bindings": {}}
            """
            return 0

        with pytest.raises(ExecutionError, match="not allowed"):
            f()


def test_frontmatter_deny_return_recognizes_leading_blank_lines(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            result = 0
            """natural

            ---
            deny:
              - return
            ---
            <:result>
            {"execution_final": {"effect": {"type": "return", "source_path": "result"}, "error": null}, "bindings": {"result": 11}}
            """
            return result

        with pytest.raises(ExecutionError, match="not allowed"):
            f()


def test_frontmatter_deny_return_allows_bindings(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f(x: int):
            computed_result = x + 1
            envelope_json_text = json.dumps(
                {
                    "execution_final": {"effect": None, "error": None},
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


def test_inline_fstring_natural_block_can_access_locals(tmp_path: Path):
    create_workspace_directories(tmp_path)

    from nighthawk.execution.contracts import ExecutionFinal

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    @dataclass
    class RecordingExecutor:
        seen_programs: list[str] = field(default_factory=list)

        def run_natural_block(
            self,
            *,
            processed_natural_program: str,
            execution_context: object,
            binding_names: list[str],
            is_in_loop: bool,
            allowed_effect_types: tuple[str, ...] = ("return", "break", "continue"),
        ) -> tuple[ExecutionFinal, dict[str, object]]:
            _ = execution_context
            _ = binding_names
            _ = is_in_loop
            _ = allowed_effect_types
            self.seen_programs.append(processed_natural_program)
            return ExecutionFinal(effect=None, error=None), {"result": 2}

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
        def f() -> int:
            result = 0
            shadowed_number = 2
            f"""natural
            <:result>
            value={shadowed_number}
            """
            _ = shadowed_number
            return result

        assert f() == 2

    assert recording_executor.seen_programs == ["<:result>\nvalue=2\n"]


def test_inline_fstring_natural_block_can_call_local_and_global_helpers(tmp_path: Path) -> None:
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def function_under_test() -> int:
            def local_import_file(file_path: Path | str) -> str:
                _ = file_path
                return '{"execution_final": {"effect": null, "error": null}, "bindings": {"result": 10}}'

            result = 0

            f"""natural
            <:result>
            {local_import_file("ignored.md")}
            """

            f"""natural
            <:result>
            {global_import_file("ignored.md")}
            """

            return result

        assert function_under_test() == 20


def test_enclosing_scope_capture_is_isolated_between_factories(tmp_path: Path) -> None:
    create_workspace_directories(tmp_path)

    from nighthawk.execution.contracts import ExecutionFinal

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    @dataclass
    class RecordingExecutor:
        seen_programs: list[str] = field(default_factory=list)

        def run_natural_block(
            self,
            *,
            processed_natural_program: str,
            execution_context: object,
            binding_names: list[str],
            is_in_loop: bool,
            allowed_effect_types: tuple[str, ...] = ("return", "break", "continue"),
        ) -> tuple[ExecutionFinal, dict[str, object]]:
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

        def factory(n: int) -> object:
            def helper() -> str:
                return f"value:{n}"

            _ = helper

            @nh.fn
            def f() -> None:
                f"""natural
                {helper()}
                """

            return f

        f1 = factory(1)
        f2 = factory(2)
        f1()  # type: ignore[operator]
        f2()  # type: ignore[operator]

    assert recording_executor.seen_programs == ["value:1\n", "value:2\n"]


def test_input_binding_can_resolve_enclosing_scope_name(tmp_path: Path) -> None:
    create_workspace_directories(tmp_path)

    from nighthawk.execution.context import ExecutionContext
    from nighthawk.execution.contracts import ExecutionFinal

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    @dataclass
    class AssertingExecutor:
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
            _ = binding_names
            _ = is_in_loop
            _ = allowed_effect_types

            assert execution_context.execution_locals["x"] == 123
            return ExecutionFinal(effect=None, error=None), {}

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=AssertingExecutor(),
            memory=RuntimeMemory(),
            workspace_root=tmp_path,
        )
    ):

        def factory(x: int) -> object:
            _ = x

            @nh.fn
            def f() -> None:
                """natural
                <x>
                Hello.
                """

            return f

        f = factory(123)
        f()  # type: ignore[operator]


def test_frontmatter_deny_continue_in_loop_rejects_continue_effect(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            total = 0
            for _ in range(5):
                total += 1
                """natural
                ---
                deny:
                  - continue
                ---
                {"execution_final": {"effect": {"type": "continue", "source_path": null}, "error": null}, "bindings": {}}
                """
                total += 100
            return total

        with pytest.raises(ExecutionError, match="not allowed"):
            f()


def test_fn_updates_output_binding_via_inline_fstring_natural_block(tmp_path: Path):
    create_workspace_directories(tmp_path)

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=StubExecutor(),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f(x: int):
            _ = x
            result = 0

            computed_result = x * 2
            envelope_json_text = json.dumps(
                {
                    "execution_final": {"effect": None, "error": None},
                    "bindings": {"result": computed_result},
                }
            )

            f"""natural
            <:result>
            {envelope_json_text}
            """
            return result

        assert f(6) == 12


def test_dotted_mutation_is_independent_of_commit_selection(tmp_path: Path):
    create_workspace_directories(tmp_path)

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):
            from nighthawk.execution.contracts import ExecutionFinal
            from nighthawk.tools.assignment import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            assign_result = assign_tool(deps, "obj.field", "123")
            assert assign_result["updates"] == [{"path": "obj.field", "value": 123}]

            return FakeRunResult(ExecutionFinal(effect=None, error=None))

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    agent = FakeAgent()

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=agent),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> int:
            class Obj:
                def __init__(self):
                    self.field = 0

            obj = Obj()

            """natural
            Mutate obj.field.
            """

            return obj.field

        assert f() == 123


def test_agent_backend_is_used_by_default(tmp_path: Path):
    create_workspace_directories(tmp_path)

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):
            from nighthawk.execution.contracts import ExecutionEffect, ExecutionFinal
            from nighthawk.tools.assignment import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs
            assign_tool(deps, "result", "x + 1")

            # Prove that a normal Python function can read ExecutionContext via ContextVar
            # while the agent run is executing.
            from nighthawk import get_current_execution_context

            execution_context = get_current_execution_context()
            assert execution_context.execution_locals["result"] == 11

            return FakeRunResult(
                ExecutionFinal(
                    effect=ExecutionEffect(type="return", source_path="result"),
                    error=None,
                )
            )

    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )
    memory = RuntimeMemory()
    agent = FakeAgent()

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=agent),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f(x: int):
            _ = x
            result = 0

            computed_result = x + 1
            envelope_json_text = json.dumps(
                {
                    "execution_final": {"effect": None, "error": None},
                    "bindings": {"result": computed_result},
                }
            )

            f"""natural
            <:result>
            {envelope_json_text}
            """
            return result

        assert f(10) == 11
