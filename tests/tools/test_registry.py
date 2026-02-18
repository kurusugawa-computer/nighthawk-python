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
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.runtime.step_contract import PassStepOutcome

            _ = user_prompt
            _ = deps
            _ = kwargs
            return FakeRunResult(PassStepOutcome(kind="pass"))

    agent = FakeAgent()

    from nighthawk.tools.registry import get_visible_tools

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=nh.AgentStepExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.natural_function
        def f() -> None:
            @nh.tool(name="test_call_scoped")
            def test_call_scoped(run_context) -> str:  # type: ignore[no-untyped-def]
                _ = run_context
                return "ok"

            """natural
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
            """

        f()

    # After the decorated call returns, the call-scoped tool should not leak.
    names = [t.name for t in get_visible_tools()]
    assert "test_call_scoped" not in names


def test_call_scoped_tools_added_mid_call_are_visible_next_block(tmp_path):
    configuration = nh.NighthawkConfiguration(
        run_configuration=nh.RunConfiguration(),
    )

    class FakeRunResult:
        def __init__(self, output):
            self.output = output

    class FakeAgent:
        def __init__(self):
            self.seen_tool_names: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, toolsets=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.runtime.step_contract import PassStepOutcome

            _ = user_prompt
            _ = deps
            _ = kwargs
            assert toolsets is not None
            toolset = toolsets[0]
            self.seen_tool_names.append(",".join(sorted(toolset.tools.keys())))

            return FakeRunResult(PassStepOutcome(kind="pass"))

    agent = FakeAgent()

    with nh.run(
        nh.Environment(
            run_configuration=configuration.run_configuration,
            step_executor=nh.AgentStepExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.natural_function
        def f() -> None:
            """natural
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
            """

            @nh.tool(name="test_late_global", overwrite=True)
            def test_late_global(run_context) -> str:  # type: ignore[no-untyped-def]
                _ = run_context
                return "late"

            """natural
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
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

        @nh.tool(name="nh_eval")
        def bad_tool(run_context, expression: str) -> str:  # type: ignore[no-untyped-def]
            _ = run_context
            _ = expression
            return "nope"
