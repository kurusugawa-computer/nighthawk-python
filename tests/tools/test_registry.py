from __future__ import annotations

import pytest

import nighthawk as nh
from nighthawk.errors import ToolRegistrationError


def test_tool_registers_globally_without_environment():
    @nh.tool(name="test_global")
    def test_global(run_context, *, target: str) -> str:  # type: ignore[no-untyped-def]
        return f"hello, {target}"

    # It should be visible even outside runtime context.
    from nighthawk.tools.registry import get_visible_tools

    names = [t.name for t in get_visible_tools()]
    assert "test_global" in names


def test_tool_name_conflict_requires_overwrite():
    @nh.tool(name="test_conflict")
    def test_conflict_1(run_context) -> str:  # type: ignore[no-untyped-def]
        return "v1"

    with pytest.raises(ToolRegistrationError):

        @nh.tool(name="test_conflict")
        def test_conflict_2(run_context) -> str:  # type: ignore[no-untyped-def]
            return "v2"


def test_tool_name_conflict_allows_overwrite_true():
    @nh.tool(name="test_overwrite")
    def test_overwrite_1(run_context) -> str:  # type: ignore[no-untyped-def]
        return "v1"

    @nh.tool(name="test_overwrite", overwrite=True)
    def test_overwrite_2(run_context) -> str:  # type: ignore[no-untyped-def]
        return "v2"


def test_tool_defined_in_call_scope_is_not_global(tmp_path):
    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.execution.contracts import ExecutionFinal

            _ = user_prompt
            _ = deps
            _ = kwargs
            return FakeRunResult(ExecutionFinal(effect=None, error=None))

    agent = FakeAgent()

    from nighthawk.tools.registry import get_visible_tools

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            @nh.tool(name="test_call_scoped")
            def test_call_scoped(run_context) -> str:  # type: ignore[no-untyped-def]
                _ = run_context
                return "ok"

            """natural
            {"execution_final": {"effect": null, "error": null}, "bindings": {}}
            """

        f()

    # After the decorated call returns, the call-scoped tool should not leak.
    names = [t.name for t in get_visible_tools()]
    assert "test_call_scoped" not in names


def test_call_scoped_tools_added_mid_call_are_visible_next_block(tmp_path):
    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def __init__(self):
            self.seen_tool_names: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, toolsets=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.execution.contracts import ExecutionFinal

            _ = user_prompt
            _ = deps
            _ = kwargs
            assert toolsets is not None
            toolset = toolsets[0]
            self.seen_tool_names.append(",".join(sorted(toolset.tools.keys())))

            return FakeRunResult(ExecutionFinal(effect=None, error=None))

    agent = FakeAgent()

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            """natural
            {"execution_final": {"effect": null, "error": null}, "bindings": {}}
            """

            @nh.tool(name="test_late_global", overwrite=True)
            def test_late_global(run_context) -> str:  # type: ignore[no-untyped-def]
                _ = run_context
                return "late"

            """natural
            {"execution_final": {"effect": null, "error": null}, "bindings": {}}
            """

        f()

    assert len(agent.seen_tool_names) == 2
    assert "test_late_global" not in agent.seen_tool_names[0]
    assert "test_late_global" in agent.seen_tool_names[1]


def test_builtin_tools_are_always_visible():
    from nighthawk.tools.registry import get_visible_tools

    names = {t.name for t in get_visible_tools()}

    assert "nh_dir" in names
    assert "nh_help" in names
    assert "nh_eval" in names
    assert "nh_assign" in names


def test_builtin_tool_name_conflict_requires_overwrite():
    with pytest.raises(ToolRegistrationError):

        @nh.tool(name="nh_dir")
        def user_nh_dir(run_context) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "user"


def test_assign_tool_allows_non_binding_local_target():
    from nighthawk.execution.context import ExecutionContext
    from nighthawk.tools.assignment import assign_tool

    execution_context = ExecutionContext(
        execution_id="test_assign_tool_allows_non_binding_local_target",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals={"__builtins__": __builtins__},
        execution_locals={},
        binding_commit_targets=set(),
    )

    result = assign_tool(execution_context, "now", "123")
    assert result["target_path"] == "now"
    assert result["updates"] == [{"path": "now", "value": 123}]
    assert execution_context.execution_locals["now"] == 123


def test_assign_tool_rejects_reserved_local_targets():
    from nighthawk.execution.context import ExecutionContext
    from nighthawk.tools.assignment import assign_tool

    execution_context = ExecutionContext(
        execution_id="test_assign_tool_rejects_reserved_local_targets",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals={"__builtins__": __builtins__},
        execution_locals={},
        binding_commit_targets=set(),
    )

    from nighthawk.tools.contracts import ToolBoundaryFailure

    result = assign_tool(execution_context, "memory", "123")
    assert result["target_path"] == "memory"
    assert result["updates"] == [{"path": "memory", "value": 123}]
    assert execution_context.execution_locals["memory"] == 123

    with pytest.raises(ToolBoundaryFailure) as error_private:
        assign_tool(execution_context, "__private", "123")
    assert error_private.value.kind == "invalid_input"
    assert "Invalid target_path" in str(error_private.value)
    assert "__private" not in execution_context.execution_locals


def test_assign_tool_validates_only_when_type_information_present():
    from nighthawk.execution.context import ExecutionContext
    from nighthawk.tools.assignment import assign_tool

    execution_context = ExecutionContext(
        execution_id="test_assign_tool_validates_only_when_type_information_present",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals={"__builtins__": __builtins__},
        execution_locals={},
        binding_commit_targets=set(),
    )

    result_no_type = assign_tool(execution_context, "count", "'1'")
    assert result_no_type["target_path"] == "count"
    assert result_no_type["updates"] == [{"path": "count", "value": "1"}]
    assert execution_context.execution_locals["count"] == "1"

    execution_context.binding_name_to_type["count"] = int

    result_with_type = assign_tool(execution_context, "count", "'2'")
    assert result_with_type["target_path"] == "count"
    assert result_with_type["updates"] == [{"path": "count", "value": 2}]
    assert execution_context.execution_locals["count"] == 2


def test_assign_tool_rejects_dunder_segments():
    from nighthawk.execution.context import ExecutionContext
    from nighthawk.tools.assignment import assign_tool

    execution_context = ExecutionContext(
        execution_id="test_assign_tool_rejects_dunder_segments",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals={"__builtins__": __builtins__},
        execution_locals={"x": object()},
        binding_commit_targets=set(),
    )

    from nighthawk.tools.contracts import ToolBoundaryFailure

    with pytest.raises(ToolBoundaryFailure) as error_dunder:
        assign_tool(execution_context, "x.__class__", "123")
    assert error_dunder.value.kind == "invalid_input"
    assert "Invalid target_path" in str(error_dunder.value)


def test_assign_tool_is_atomic_on_traversal_failure():
    from nighthawk.execution.context import ExecutionContext
    from nighthawk.tools.assignment import assign_tool

    class Root:
        pass

    root = Root()
    execution_context = ExecutionContext(
        execution_id="test_assign_tool_is_atomic_on_traversal_failure",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals={"__builtins__": __builtins__},
        execution_locals={"root": root},
        binding_commit_targets=set(),
    )

    original_revision = execution_context.execution_locals_revision

    from nighthawk.tools.contracts import ToolBoundaryFailure

    with pytest.raises(ToolBoundaryFailure) as error_traversal:
        assign_tool(execution_context, "root.child.value", "123")
    assert error_traversal.value.kind == "resolution"
    assert execution_context.execution_locals_revision == original_revision
    assert not hasattr(root, "child")


def test_assign_tool_is_atomic_on_validation_failure():
    from nighthawk.execution.context import ExecutionContext
    from nighthawk.tools.assignment import assign_tool

    execution_context = ExecutionContext(
        execution_id="test_assign_tool_is_atomic_on_validation_failure_and_never_raises",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals={"__builtins__": __builtins__},
        execution_locals={"count": 1},
        binding_commit_targets=set(),
        binding_name_to_type={"count": int},
    )

    original_revision = execution_context.execution_locals_revision

    from nighthawk.tools.contracts import ToolBoundaryFailure

    with pytest.raises(ToolBoundaryFailure) as error_validation:
        assign_tool(execution_context, "count", "'not an int'")
    assert error_validation.value.kind == "invalid_input"
    assert execution_context.execution_locals_revision == original_revision
    assert execution_context.execution_locals["count"] == 1


def test_assign_tool_validates_pydantic_fields_and_is_atomic():
    from pydantic import BaseModel

    from nighthawk.execution.context import ExecutionContext
    from nighthawk.tools.assignment import assign_tool

    class Model(BaseModel):
        n: int = 1

    model = Model(n=1)
    execution_context = ExecutionContext(
        execution_id="test_assign_tool_validates_pydantic_fields_and_is_atomic",
        execution_configuration=nh.ExecutionConfiguration(),
        execution_globals={"__builtins__": __builtins__},
        execution_locals={"model": model},
        binding_commit_targets=set(),
    )

    ok_result = assign_tool(execution_context, "model.n", "'2'")
    assert ok_result["target_path"] == "model.n"
    assert ok_result["updates"] == [{"path": "model.n", "value": 2}]
    assert model.n == 2

    original_revision = execution_context.execution_locals_revision

    from nighthawk.tools.contracts import ToolBoundaryFailure

    with pytest.raises(ToolBoundaryFailure) as error_validation:
        assign_tool(execution_context, "model.n", "'not an int'")
    assert error_validation.value.kind == "invalid_input"
    assert execution_context.execution_locals_revision == original_revision
    assert model.n == 2


def test_agent_backend_prompt_sections_are_present(tmp_path):
    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def __init__(self):
            self.seen_prompts: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.execution.contracts import ExecutionFinal

            self.seen_prompts.append(user_prompt)
            assert deps is not None
            _ = kwargs
            return FakeRunResult(ExecutionFinal(effect=None, error=None))

    agent = FakeAgent()

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            x = 10
            """natural
            Say hi.
            """
            _ = x

        f()

    assert len(agent.seen_prompts) == 1
    prompt = agent.seen_prompts[0]
    assert "<<<NH:PROGRAM>>>" in prompt
    assert "<<<NH:END_PROGRAM>>>" in prompt
    assert "<<<NH:LOCALS>>>" in prompt
    assert "<<<NH:END_LOCALS>>>" in prompt
    assert "<<<NH:GLOBALS>>>" in prompt
    assert "<<<NH:END_GLOBALS>>>" in prompt
    assert "Say hi." in prompt
    assert "x: int = 10" in prompt
    locals_section = prompt.split("<<<NH:LOCALS>>>\n", 1)[1].split("\n<<<NH:END_LOCALS>>>", 1)[0]
    assert "memory" not in locals_section
    assert "x: int = 10" in locals_section

    globals_section = prompt.split("<<<NH:GLOBALS>>>\n", 1)[1].split("\n<<<NH:END_GLOBALS>>>", 1)[0]
    assert globals_section.splitlines() == []


def test_tool_defined_in_environment_scope_is_not_global(tmp_path):
    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def __init__(self):
            self.seen_tool_names: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, toolsets=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.execution.contracts import ExecutionFinal

            _ = user_prompt
            _ = deps
            _ = kwargs
            assert toolsets is not None
            toolset = toolsets[0]
            self.seen_tool_names.append(",".join(sorted(toolset.tools.keys())))

            return FakeRunResult(ExecutionFinal(effect=None, error=None))

    agent = FakeAgent()

    from nighthawk.tools.registry import get_visible_tools

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.tool(name="test_environment_scoped")
        def test_environment_scoped(run_context) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "ok"

        _ = test_environment_scoped

        @nh.fn
        def f() -> None:
            """natural
            This is a test.
            """

        f()

        names_in_environment = {t.name for t in get_visible_tools()}
        assert "test_environment_scoped" in names_in_environment

    names_after_environment = {t.name for t in get_visible_tools()}
    assert "test_environment_scoped" not in names_after_environment

    assert len(agent.seen_tool_names) == 1
    assert "test_environment_scoped" in agent.seen_tool_names[0]


def test_environment_override_tool_scope_does_not_leak(tmp_path):
    configuration = nh.Configuration(
        execution_configuration=nh.ExecutionConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.execution.contracts import ExecutionFinal

            _ = user_prompt
            _ = deps
            _ = kwargs
            return FakeRunResult(ExecutionFinal(effect=None, error=None))

    agent = FakeAgent()

    from nighthawk.tools.registry import get_visible_tools

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=configuration.execution_configuration,
            execution_executor=nh.AgentExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.tool(name="test_env_outer")
        def test_env_outer(run_context) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "outer"

        _ = test_env_outer

        names_outer = {t.name for t in get_visible_tools()}
        assert "test_env_outer" in names_outer
        assert "test_env_inner" not in names_outer

        with nh.environment_override(workspace_root=tmp_path):

            @nh.tool(name="test_env_inner")
            def test_env_inner(run_context) -> str:  # type: ignore[no-untyped-def]
                _ = run_context
                return "inner"

            _ = test_env_inner

            names_inner = {t.name for t in get_visible_tools()}
            assert "test_env_outer" in names_inner
            assert "test_env_inner" in names_inner

        names_after_override = {t.name for t in get_visible_tools()}
        assert "test_env_outer" in names_after_override
        assert "test_env_inner" not in names_after_override
