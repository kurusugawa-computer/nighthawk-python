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


def create_workspace_directories(workspace_root: Path) -> None:
    (workspace_root / "docs").mkdir()
    (workspace_root / "tests").mkdir()


def test_fn_updates_output_binding_via_docstring_natural_block(tmp_path: Path):
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
            """natural
            <:result>
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {x + 1}}}}}
            """
            return result

        assert f(10) == 11


def test_stub_return_effect_parses_and_coerces_value_json(tmp_path: Path):
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
            {{"execution_final": {{"effect": {{"type": "return", "value_json": "11"}}, "error": null}}, "bindings": {{}}}}
            """
            return 0

        assert f() == 11


def test_stub_return_effect_invalid_value_json_raises(tmp_path: Path):
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
            {{"execution_final": {{"effect": {{"type": "return", "value_json": "\\\"not an int\\\""}}, "error": null}}, "bindings": {{}}}}
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
                {{"execution_final": {{"effect": {{"type": "continue", "value_json": null}}, "error": null}}, "bindings": {{}}}}
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
                {{"execution_final": {{"effect": {{"type": "break", "value_json": null}}, "error": null}}, "bindings": {{}}}}
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
            {{"execution_final": {{"effect": {{"type": "break", "value_json": null}}, "error": null}}, "bindings": {{}}}}
            """
            return 1

        with pytest.raises(ExecutionError):
            f()


def test_template_preprocessing_can_access_module_globals(tmp_path: Path):
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
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {GLOBAL_NUMBER}}}}}
            """
            result = 0
            return result

        assert f() == 7


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
            {{"execution_final": {{"effect": {{"type": "return", "value_json": "11"}}, "error": null}}, "bindings": {{}}}}
            """
            return 0

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
            """natural
            ---
            deny:
              - return
            ---
            <:result>
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {x + 1}}}}}
            """
            _ = x
            result = 0
            return result

        assert f(10) == 11


def test_template_preprocessing_locals_shadow_globals(tmp_path: Path):
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
            SHADOWED_NUMBER = 2
            """natural
            <:result>
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {SHADOWED_NUMBER}}}}}
            """
            _ = SHADOWED_NUMBER
            return result

        assert f() == 2


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
                {{"execution_final": {{"effect": {{"type": "continue", "value_json": null}}, "error": null}}, "bindings": {{}}}}
                """
                total += 100
            return total

        with pytest.raises(ExecutionError, match="not allowed"):
            f()


def test_fn_updates_output_binding_via_inline_natural_block(tmp_path: Path):
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
            """natural
            <:result>
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {x * 2}}}}}
            """
            _ = x
            result = 0
            return result

        assert f(6) == 12


def test_compile_time_type_information_is_available_to_assign_tool(tmp_path: Path):
    create_workspace_directories(tmp_path)

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):
            from nighthawk.execution.llm import ExecutionFinal
            from nighthawk.tools import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            assign_result = assign_tool(deps, "count", "'2'")
            assert assign_result["ok"] is True
            assert deps.execution_locals["count"] == 2

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
            """natural
            <:count>
            Set count.
            """
            count: int = 0
            return count

        assert f() == 2


def test_dotted_mutation_is_independent_of_commit_selection(tmp_path: Path):
    create_workspace_directories(tmp_path)

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):
            from nighthawk.execution.llm import ExecutionFinal
            from nighthawk.tools import assign_tool

            assert deps is not None
            _ = user_prompt
            _ = kwargs

            assign_result = assign_tool(deps, "obj.field", "123")
            assert assign_result["ok"] is True

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
            from nighthawk.execution.llm import ExecutionEffect, ExecutionFinal
            from nighthawk.tools import assign_tool

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
                    effect=ExecutionEffect(type="return", value_json="11"),
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
            """natural
            <:result>
            {{"execution_final": {{"effect": null, "error": null}}, "bindings": {{"result": {x + 1}}}}}
            """
            _ = x
            result = 0
            return result

        assert f(10) == 11
