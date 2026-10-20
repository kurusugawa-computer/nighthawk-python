from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.tools import Tool

import nighthawk as nh
from nighthawk.runtime.scoping import _current_task_references
from tests.support.executors import StubExecutor
from tests.support.values import IdentityOnly


def lookup() -> int:
    return 1


def search() -> int:
    return 2


@pytest.mark.parametrize(
    "field",
    [
        "tools",
        "task_references",
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
        identity = nh.get_execution_reference()
        meter = nh.get_usage_meter()
        for value in (None, nh.Merge({}), nh.Extend([])):
            if (field == "task_references" and isinstance(value, nh.Merge)) or (
                field in {"tools", "capabilities", "system_prompt_suffix_fragments", "user_prompt_suffix_fragments"} and isinstance(value, nh.Extend)
            ):
                continue
            with pytest.raises(TypeError), nh.scope(**{field: value}):  # type: ignore[arg-type]
                pass
            assert nh.get_execution_reference() is identity
            assert nh.get_step_executor() is executor
            assert nh.get_usage_meter() is meter


def test_mixed_composition_and_declaration_identity() -> None:
    value = IdentityOnly()
    hooks = nh.oversight.Oversight()
    with (
        nh.run(StubExecutor()),
        nh.scope(tools=[lookup], task_references={"value": value}, oversight=hooks, system_prompt_suffix_fragments=["parent"]),
    ):
        original_tool = nh.get_tools()[0]
        with nh.scope(
            tools=nh.Extend([lookup, original_tool, search]),
            task_references=nh.Merge({"value": value}),
            system_prompt_suffix_fragments=nh.Extend(["child"]),
            user_prompt_suffix_fragments=[],
            oversight=None,
        ):
            assert nh.get_tools()[0] is original_tool
            assert [tool.name for tool in nh.get_tools()] == ["lookup", "search"]
            assert nh.get_system_prompt_suffix_fragments() == ("parent", "child")
            assert _current_task_references()["value"] is value
            assert nh.get_oversight() is None
        assert nh.get_oversight() is hooks
        with pytest.raises(nh.ToolNameConflictError), nh.scope(tools=nh.Extend([Tool(lookup)])):
            pass
        with pytest.raises(nh.NameConflictError), nh.scope(task_references=nh.Merge({"value": IdentityOnly()})):
            pass
        with nh.scope(tools=[], task_references={}, system_prompt_suffix_fragments=[]):
            assert nh.get_tools() == ()
            assert _current_task_references() == {}
            assert nh.get_system_prompt_suffix_fragments() == ()
        assert nh.get_tools()[0] is original_tool


def test_explicit_tool_duplicates_and_namespace_independence() -> None:
    tool = Tool(lookup)
    with nh.run(StubExecutor()), nh.scope(tools=[tool, tool], task_references={"lookup": object()}):
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


def test_task_references_are_available_through_the_active_environment() -> None:
    injected = object()
    seen: list[object] = []
    python_globals = dict(globals())

    class Executor:
        def run_step(self, *, request: nh.runtime.StepRequest) -> nh.runtime.StepCandidate:
            environment = request.step_context.environment
            assert environment.resolve("len") is len
            assert environment.resolve("len", source="task") is injected
            assert environment.task_name_to_value == {"len": injected}
            seen.append(environment.task_name_to_value["len"])
            with pytest.raises(TypeError):
                environment.task_name_to_value["len"] = None  # type: ignore[index]
            return nh.runtime.StepCandidate(outcome=nh.runtime.Pass())

    @nh.function
    def workflow() -> None:
        """execute
        Inspect the visible environment.
        """

    with nh.run(Executor()), nh.scope(task_references={"len": injected}):
        workflow()
    assert seen == [injected]
    assert globals().keys() == python_globals.keys()
    assert all(globals()[name] is value for name, value in python_globals.items())
