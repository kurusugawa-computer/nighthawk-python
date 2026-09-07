from __future__ import annotations

import pytest
from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

import nighthawk as nh
from nighthawk.errors import NighthawkError, ToolRegistrationError
from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.registry import get_visible_tools
from tests.execution.stub_executor import StubExecutor


def _visible_tool_name_set() -> set[str]:
    return {tool.name for tool in get_visible_tools()}


def greet(target: str) -> str:
    """Return a greeting."""
    return f"hello, {target}"


def read_step_id(run_context: RunContext[StepContext]) -> str:
    return run_context.deps.step_id


def test_builtin_tools_are_always_visible() -> None:
    assert {"nh_eval", "nh_assign"} <= _visible_tool_name_set()

    with nh.run(StubExecutor()):
        assert {"nh_eval", "nh_assign"} <= _visible_tool_name_set()
        with nh.scope(mode="replace", tools=[]):
            assert _visible_tool_name_set() == {"nh_eval", "nh_assign"}


def test_get_tools_requires_active_run() -> None:
    with pytest.raises(NighthawkError):
        nh.get_tools()


def test_scope_tools_are_visible_only_inside_the_scope() -> None:
    with nh.run(StubExecutor()):
        assert nh.get_tools() == ()
        assert "greet" not in _visible_tool_name_set()

        with nh.scope(tools=[greet]):
            assert [tool.name for tool in nh.get_tools()] == ["greet"]
            assert "greet" in _visible_tool_name_set()

        assert nh.get_tools() == ()
        assert "greet" not in _visible_tool_name_set()


def test_plain_callable_is_wrapped_with_pydantic_ai_tool_defaults() -> None:
    with nh.run(StubExecutor()), nh.scope(tools=[greet, read_step_id]):
        tool_name_to_tool = {tool.name: tool for tool in nh.get_tools()}
        assert tool_name_to_tool["greet"].description == "Return a greeting."
        assert tool_name_to_tool["greet"].takes_ctx is False
        assert tool_name_to_tool["read_step_id"].takes_ctx is True


def test_tool_instance_keeps_custom_name_and_description() -> None:
    custom_tool: Tool[StepContext] = Tool(greet, name="custom_greet", description="Custom description.")

    with nh.run(StubExecutor()), nh.scope(tools=[custom_tool]):
        (tool,) = nh.get_tools()
        assert tool is custom_tool
        assert tool.name == "custom_greet"
        assert tool.description == "Custom description."


def test_inherit_mode_appends_tools_from_outer_scopes() -> None:
    with nh.run(StubExecutor()), nh.scope(tools=[greet]), nh.scope(tools=[read_step_id]):
        assert [tool.name for tool in nh.get_tools()] == ["greet", "read_step_id"]


def test_replace_mode_hides_outer_tools() -> None:
    with nh.run(StubExecutor()), nh.scope(tools=[greet]):
        with nh.scope(mode="replace", tools=[read_step_id]):
            assert [tool.name for tool in nh.get_tools()] == ["read_step_id"]
            assert "greet" not in _visible_tool_name_set()

        with nh.scope(mode="replace", tools=None):
            assert [tool.name for tool in nh.get_tools()] == ["greet"]

        assert [tool.name for tool in nh.get_tools()] == ["greet"]


def test_duplicate_tool_name_raises_in_inherit_mode() -> None:
    with (
        nh.run(StubExecutor()),
        nh.scope(tools=[greet]),
        pytest.raises(ToolRegistrationError, match="Tool name conflict"),
        nh.scope(tools=[Tool(read_step_id, name="greet")]),
    ):
        pass


def test_duplicate_tool_name_within_one_scope_raises() -> None:
    with (
        nh.run(StubExecutor()),
        pytest.raises(ToolRegistrationError, match="Tool name conflict"),
        nh.scope(tools=[greet, Tool(read_step_id, name="greet")]),
    ):
        pass


def test_builtin_tool_name_cannot_be_shadowed() -> None:
    with (
        nh.run(StubExecutor()),
        pytest.raises(ToolRegistrationError, match="built-in"),
        nh.scope(mode="replace", tools=[Tool(greet, name="nh_eval")]),
    ):
        pass


def test_invalid_tool_name_raises() -> None:
    with (
        nh.run(StubExecutor()),
        pytest.raises(ToolRegistrationError, match="must match"),
        nh.scope(tools=[Tool(greet, name="not-valid")]),
    ):
        pass


def test_agent_step_executor_receives_scoped_tools_in_toolset() -> None:
    class FakeRunResult:
        def __init__(self, output: object) -> None:
            self.output = output

    class FakeAgent:
        def __init__(self) -> None:
            self.seen_tool_names: list[str] = []

        def run_sync(self, user_prompt, *, deps=None, toolsets=None, **kwargs):  # type: ignore[no-untyped-def]
            from nighthawk.runtime.step_contract import PassStepOutcome, StepFinalResult

            _ = (user_prompt, deps, kwargs)
            assert toolsets is not None
            self.seen_tool_names.append(",".join(sorted(toolsets[0].tools.keys())))
            return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))

    agent = FakeAgent()

    @nh.natural_function
    def natural_pass_function() -> None:
        """natural
        Do nothing.
        """

    with nh.run(nh.AgentStepExecutor.from_agent(agent=agent)):
        natural_pass_function()
        with nh.scope(tools=[greet]):
            natural_pass_function()
        natural_pass_function()

    assert agent.seen_tool_names == [
        "nh_assign,nh_eval",
        "greet,nh_assign,nh_eval",
        "nh_assign,nh_eval",
    ]


def test_tool_decorator_is_removed_from_public_api() -> None:
    assert not hasattr(nh, "tool")
    assert "tool" not in nh.__all__
