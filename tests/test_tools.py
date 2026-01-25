from __future__ import annotations

import pytest
from pydantic import BaseModel

import nighthawk as nh
from nighthawk.core import ToolRegistrationError


class FakeMemory(BaseModel):
    pass


def test_tool_registers_globally_without_environment():
    @nh.tool(name="test_global")
    def test_global(run_context, *, target: str) -> str:  # type: ignore[no-untyped-def]
        return f"hello, {target}"

    # It should be visible even outside runtime context.
    from nighthawk.tools import get_visible_tools

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
        model="openai:gpt-5-nano",
    )
    memory = FakeMemory()

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.llm import NaturalFinal

            return FakeRunResult(NaturalFinal(effect=None, error=None))

    agent = FakeAgent()

    from nighthawk.tools import get_visible_tools

    with nh.environment(
        nh.Environment(
            configuration=configuration,
            natural_executor=nh.AgentExecutor(agent=agent),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            @nh.tool(name="test_call_scoped")
            def test_call_scoped(run_context) -> str:  # type: ignore[no-untyped-def]
                return "ok"

            """natural
            {{"natural_final": {{"effect": null, "error": null}}, "bindings": {{}}}}
            """

        f()

    # After the decorated call returns, the call-scoped tool should not leak.
    names = [t.name for t in get_visible_tools()]
    assert "test_call_scoped" not in names


def test_call_scoped_tools_added_mid_call_are_visible_next_block(tmp_path):
    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = FakeMemory()

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def __init__(self):
            self.seen_tool_names: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, toolsets=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.llm import NaturalFinal

            assert toolsets is not None
            toolset = toolsets[0]
            self.seen_tool_names.append(",".join(sorted(toolset.tools.keys())))

            return FakeRunResult(NaturalFinal(effect=None, error=None))

    agent = FakeAgent()

    with nh.environment(
        nh.Environment(
            configuration=configuration,
            natural_executor=nh.AgentExecutor(agent=agent),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            """natural
            {{"natural_final": {{"effect": null, "error": null}}, "bindings": {{}}}}
            """

            @nh.tool(name="test_late_global", overwrite=True)
            def test_late_global(run_context) -> str:  # type: ignore[no-untyped-def]
                return "late"

            """natural
            {{"natural_final": {{"effect": null, "error": null}}, "bindings": {{}}}}
            """

        f()

    assert len(agent.seen_tool_names) == 2
    assert "test_late_global" not in agent.seen_tool_names[0]
    assert "test_late_global" in agent.seen_tool_names[1]


def test_builtin_tools_are_always_visible():
    from nighthawk.tools import get_visible_tools

    names = {t.name for t in get_visible_tools()}

    assert "nh_dir" in names
    assert "nh_help" in names
    assert "nh_eval" in names
    assert "nh_assign" in names
    assert "nh_json_dumps" in names


def test_builtin_tool_name_conflict_requires_overwrite():
    with pytest.raises(ToolRegistrationError):

        @nh.tool(name="nh_dir")
        def user_nh_dir(run_context) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "user"


def test_assign_tool_allows_non_binding_local_target():
    from nighthawk.context import ExecutionContext
    from nighthawk.tools import assign_tool

    execution_context = ExecutionContext(
        globals={"__builtins__": __builtins__},
        locals={},
        binding_commit_targets=set(),
        memory=None,
    )

    result = assign_tool(execution_context, "<now>", "123", type_hints={})
    assert result["ok"] is True
    assert execution_context.locals["now"] == 123


def test_assign_tool_rejects_reserved_local_targets():
    from nighthawk.context import ExecutionContext
    from nighthawk.tools import assign_tool

    execution_context = ExecutionContext(
        globals={"__builtins__": __builtins__},
        locals={},
        binding_commit_targets=set(),
        memory=None,
    )

    result_memory = assign_tool(execution_context, "<memory>", "123", type_hints={})
    assert result_memory["ok"] is False
    assert "memory" not in execution_context.locals

    result_private = assign_tool(execution_context, "<__private>", "123", type_hints={})
    assert result_private["ok"] is False
    assert "__private" not in execution_context.locals


def test_assign_tool_validates_only_when_type_hints_present():
    from nighthawk.context import ExecutionContext
    from nighthawk.tools import assign_tool

    execution_context = ExecutionContext(
        globals={"__builtins__": __builtins__},
        locals={},
        binding_commit_targets=set(),
        memory=None,
    )

    result_no_hint = assign_tool(execution_context, "<count>", "'1'", type_hints={})
    assert result_no_hint["ok"] is True
    assert execution_context.locals["count"] == "1"

    result_with_hint = assign_tool(execution_context, "<count>", "'2'", type_hints={"count": int})
    assert result_with_hint["ok"] is True
    assert execution_context.locals["count"] == 2


def test_prompt_template_sections_are_present_in_agent_backend_prompt(tmp_path):
    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = FakeMemory()

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def __init__(self):
            self.seen_prompts: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.llm import NaturalFinal

            self.seen_prompts.append(user_prompt)
            assert deps is not None
            return FakeRunResult(NaturalFinal(effect=None, error=None))

    agent = FakeAgent()

    with nh.environment(
        nh.Environment(
            configuration=configuration,
            natural_executor=nh.AgentExecutor(agent=agent),
            memory=memory,
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
    assert "<<<NH:MEMORY>>>" in prompt
    assert "<<<NH:END_MEMORY>>>" in prompt
    assert "Say hi." in prompt
    assert "x = 10" in prompt
    locals_section = prompt.split("<<<NH:LOCALS>>>\n", 1)[1].split("\n<<<NH:END_LOCALS>>>", 1)[0]
    assert "memory =" not in locals_section

    memory_section = prompt.split("<<<NH:MEMORY>>>\n", 1)[1].split("\n<<<NH:END_MEMORY>>>", 1)[0]
    assert memory_section.strip() != ""


def test_tool_defined_in_environment_scope_is_not_global(tmp_path):
    configuration = nh.Configuration(
        model="openai:gpt-5-nano",
    )
    memory = FakeMemory()

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def __init__(self):
            self.seen_tool_names: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, toolsets=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.llm import NaturalFinal

            assert toolsets is not None
            toolset = toolsets[0]
            self.seen_tool_names.append(",".join(sorted(toolset.tools.keys())))

            return FakeRunResult(NaturalFinal(effect=None, error=None))

    agent = FakeAgent()

    from nighthawk.tools import get_visible_tools

    with nh.environment(
        nh.Environment(
            configuration=configuration,
            natural_executor=nh.AgentExecutor(agent=agent),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.tool(name="test_environment_scoped")
        def test_environment_scoped(run_context) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "ok"

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
        model="openai:gpt-5-nano",
    )
    memory = FakeMemory()

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.llm import NaturalFinal

            return FakeRunResult(NaturalFinal(effect=None, error=None))

    agent = FakeAgent()

    from nighthawk.tools import get_visible_tools

    with nh.environment(
        nh.Environment(
            configuration=configuration,
            natural_executor=nh.AgentExecutor(agent=agent),
            memory=memory,
            workspace_root=tmp_path,
        )
    ):

        @nh.tool(name="test_env_outer")
        def test_env_outer(run_context) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            return "outer"

        names_outer = {t.name for t in get_visible_tools()}
        assert "test_env_outer" in names_outer
        assert "test_env_inner" not in names_outer

        with nh.environment_override(workspace_root=tmp_path):

            @nh.tool(name="test_env_inner")
            def test_env_inner(run_context) -> str:  # type: ignore[no-untyped-def]
                _ = run_context
                return "inner"

            names_inner = {t.name for t in get_visible_tools()}
            assert "test_env_outer" in names_inner
            assert "test_env_inner" in names_inner

        names_after_override = {t.name for t in get_visible_tools()}
        assert "test_env_outer" in names_after_override
        assert "test_env_inner" not in names_after_override
