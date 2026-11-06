from __future__ import annotations

import pytest
from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.errors import ToolDeclarationError
from nighthawk.adapters.pydantic_ai.scoping import get_model_scope
from nighthawk.adapters.pydantic_ai.tools.declarations import get_visible_tools
from nighthawk.core.step_context import StepContext
from tests.support.executors import StubExecutor


def _visible_tool_name_set() -> set[str]:
    return {tool.name for tool in get_visible_tools()}


def greet(target: str) -> str:
    """Return a greeting."""
    return f"hello, {target}"


def read_step_execution_id(run_context: RunContext[StepContext]) -> str:
    return run_context.deps.execution_reference.step_execution_id or ""


def test_builtin_tools_are_always_visible() -> None:
    assert {"nh_evaluate", "nh_assign"} <= _visible_tool_name_set()

    with nh.run(StubExecutor()):
        assert {"nh_evaluate", "nh_assign"} <= _visible_tool_name_set()
        with nh.scope(tools=[]):
            assert _visible_tool_name_set() == {"nh_evaluate", "nh_assign"}


def test_scope_tools_are_visible_only_inside_the_scope() -> None:
    with nh.run(StubExecutor()):
        assert get_model_scope().tools == ()
        assert "greet" not in _visible_tool_name_set()

        with nh.scope(tools=[greet]):
            assert [tool.name for tool in get_model_scope().tools] == ["greet"]
            assert "greet" in _visible_tool_name_set()

        assert get_model_scope().tools == ()
        assert "greet" not in _visible_tool_name_set()


def test_plain_callable_is_wrapped_with_pydantic_ai_tool_defaults() -> None:
    with nh.run(StubExecutor()), nh.scope(tools=[greet, read_step_execution_id]):
        tool_name_to_tool = {tool.name: tool for tool in get_model_scope().tools}
        assert tool_name_to_tool["greet"].description == "Return a greeting."
        assert tool_name_to_tool["greet"].takes_ctx is False
        assert tool_name_to_tool["read_step_execution_id"].takes_ctx is True


def test_tool_instance_keeps_custom_name_and_description() -> None:
    custom_tool: Tool[StepContext] = Tool(greet, name="custom_greet", description="Custom description.")

    with nh.run(StubExecutor()), nh.scope(tools=[custom_tool]):
        (tool,) = get_model_scope().tools
        assert tool is custom_tool
        assert tool.name == "custom_greet"
        assert tool.description == "Custom description."


def test_extend_appends_tools_from_outer_scopes() -> None:
    with nh.run(StubExecutor()), nh.scope(tools=[greet]), nh.scope(tools=nh.Extend([read_step_execution_id])):
        assert [tool.name for tool in get_model_scope().tools] == ["greet", "read_step_execution_id"]


def test_sequence_replaces_tools_and_unset_inherits() -> None:
    with nh.run(StubExecutor()), nh.scope(tools=[greet]):
        with nh.scope(tools=[read_step_execution_id]):
            assert [tool.name for tool in get_model_scope().tools] == ["read_step_execution_id"]
            assert "greet" not in _visible_tool_name_set()

        with nh.scope(tools=nh.UNSET):
            assert [tool.name for tool in get_model_scope().tools] == ["greet"]

        assert [tool.name for tool in get_model_scope().tools] == ["greet"]


def test_extend_rejects_duplicate_tool_names() -> None:
    with (
        nh.run(StubExecutor()),
        nh.scope(tools=[greet]),
        pytest.raises(ToolDeclarationError, match="Tool name conflict"),
        nh.scope(tools=nh.Extend([Tool(read_step_execution_id, name="greet")])),
    ):
        pass


def test_duplicate_tool_name_within_one_scope_raises() -> None:
    with (
        nh.run(StubExecutor()),
        pytest.raises(ToolDeclarationError, match="Tool name conflict"),
        nh.scope(tools=[greet, Tool(read_step_execution_id, name="greet")]),
    ):
        pass


def test_builtin_tool_name_cannot_be_shadowed() -> None:
    with (
        nh.run(StubExecutor()),
        pytest.raises(ToolDeclarationError, match="built-in"),
        nh.scope(tools=[Tool(greet, name="nh_evaluate")]),
    ):
        pass


def test_invalid_tool_name_raises() -> None:
    with (
        nh.run(StubExecutor()),
        pytest.raises(ToolDeclarationError, match="must match"),
        nh.scope(tools=[Tool(greet, name="not-valid")]),
    ):
        pass
