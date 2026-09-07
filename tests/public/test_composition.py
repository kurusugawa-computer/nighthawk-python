from __future__ import annotations

import asyncio
from copy import copy, deepcopy
from dataclasses import FrozenInstanceError

import pytest
from pydantic_ai.tools import Tool

import nighthawk as nh
from tests.execution.stub_executor import StubExecutor


def lookup() -> int:
    return 1


def search() -> int:
    return 2


class IdentityOnly:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("Declaration comparison must use identity")


def test_wrappers_capture_structure_and_preserve_identity() -> None:
    value = IdentityOnly()
    values = [value]
    name_to_value = {"value": value}
    extension = nh.Extend(values)
    merge = nh.Merge(name_to_value)
    values.clear()
    name_to_value.clear()
    assert extension.values[0] is value
    assert merge.name_to_value["value"] is value
    assert copy(nh.UNSET) is deepcopy(nh.UNSET) is nh.UnsetType() is nh.UNSET
    with pytest.raises(FrozenInstanceError):
        extension.values = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        merge.name_to_value["other"] = value  # type: ignore[index]


@pytest.mark.parametrize("value", [None, "abc", b"abc", {1, 2}, iter([1])])
def test_extend_rejects_non_sequences(value: object) -> None:
    with pytest.raises(TypeError):
        nh.Extend(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "tools",
        "implicit_references",
        "capabilities",
        "system_prompt_suffix_fragments",
        "user_prompt_suffix_fragments",
        "step_executor",
        "step_executor_configuration",
        "usage_meter",
    ],
)
def test_invalid_scope_arguments_leave_parent_intact(field: str) -> None:
    executor = StubExecutor()
    with nh.run(executor):
        identity = nh.get_execution_ref()
        meter = nh.get_usage_meter()
        for value in (None, nh.Merge({}), nh.Extend([])):
            if (field == "implicit_references" and isinstance(value, nh.Merge)) or (
                field in {"tools", "capabilities", "system_prompt_suffix_fragments", "user_prompt_suffix_fragments"} and isinstance(value, nh.Extend)
            ):
                continue
            with pytest.raises(TypeError), nh.scope(**{field: value}):  # type: ignore[arg-type]
                pass
            assert nh.get_execution_ref() is identity
            assert nh.get_step_executor() is executor
            assert nh.get_usage_meter() is meter


def test_mixed_composition_and_declaration_identity() -> None:
    value = IdentityOnly()
    hooks = nh.oversight.Oversight()
    with (
        nh.run(StubExecutor()),
        nh.scope(tools=[lookup], implicit_references={"value": value}, oversight=hooks, system_prompt_suffix_fragments=["parent"]),
    ):
        original_tool = nh.get_tools()[0]
        with nh.scope(
            tools=nh.Extend([lookup, original_tool, search]),
            implicit_references=nh.Merge({"value": value}),
            system_prompt_suffix_fragments=nh.Extend(["child"]),
            user_prompt_suffix_fragments=[],
            oversight=None,
        ):
            assert nh.get_tools()[0] is original_tool
            assert [tool.name for tool in nh.get_tools()] == ["lookup", "search"]
            assert nh.get_system_prompt_suffix_fragments() == ("parent", "child")
            assert nh.get_implicit_references()["value"] is value
            assert nh.get_oversight() is None
        assert nh.get_oversight() is hooks
        with pytest.raises(nh.ToolNameConflictError), nh.scope(tools=nh.Extend([Tool(lookup)])):
            pass
        with pytest.raises(nh.NameConflictError), nh.scope(implicit_references=nh.Merge({"value": IdentityOnly()})):
            pass
        with nh.scope(tools=[], implicit_references={}, system_prompt_suffix_fragments=[]):
            assert nh.get_tools() == ()
            assert nh.get_implicit_references() == {}
            assert nh.get_system_prompt_suffix_fragments() == ()
        assert nh.get_tools()[0] is original_tool


def test_explicit_tool_duplicates_and_namespace_independence() -> None:
    tool = Tool(lookup)
    with nh.run(StubExecutor()), nh.scope(tools=[tool, tool], implicit_references={"lookup": object()}):
        assert nh.get_tools() == (tool,)
        with pytest.raises(nh.ToolDeclarationError), nh.scope(tools=nh.Extend([lookup])):
            pass
        with pytest.raises(nh.NameConflictError), nh.scope(tools=[Tool(lookup, name="nh_eval")]):
            pass
    assert issubclass(nh.ToolNameConflictError, nh.ToolDeclarationError)
    assert issubclass(nh.ToolNameConflictError, nh.NameConflictError)


def test_async_scopes_restore_and_isolate() -> None:
    async def worker(label: str) -> None:
        with nh.scope(system_prompt_suffix_fragments=[label]):
            await asyncio.sleep(0)
            assert nh.get_system_prompt_suffix_fragments() == (label,)
            with pytest.raises(RuntimeError), nh.scope(system_prompt_suffix_fragments=[]):
                raise RuntimeError("child")
            assert nh.get_system_prompt_suffix_fragments() == (label,)

    async def execute() -> None:
        with nh.run(StubExecutor()):
            await asyncio.gather(worker("first"), worker("second"))
            assert nh.get_system_prompt_suffix_fragments() == ()

    asyncio.run(execute())


def test_required_getter_lifetimes_and_meter_none() -> None:
    for getter in (nh.get_usage_meter, nh.get_step_context, nh.get_oversight):
        with pytest.raises(nh.NighthawkError):
            getter()
    with pytest.raises(TypeError), nh.run(StubExecutor(), usage_meter=None):  # type: ignore[arg-type]
        pass
    with nh.run(StubExecutor()):
        with pytest.raises(nh.NighthawkError):
            nh.get_step_context()
        assert nh.get_oversight() is None
