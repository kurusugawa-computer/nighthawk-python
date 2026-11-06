from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.tools import Tool

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.errors import ToolDeclarationError, ToolNameConflictError
from nighthawk.adapters.pydantic_ai.scoping import get_model_scope
from nighthawk.core import oversight
from nighthawk.core.errors import NameConflictError
from nighthawk.core.scoping import get_scope
from nighthawk.core.step_context import get_step_context
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
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
        identity = get_scope().execution_reference
        meter = get_scope().usage_meter
        for value in (None, nh.Merge({}), nh.Extend([])):
            if (field == "task_references" and isinstance(value, nh.Merge)) or (
                field in {"tools", "capabilities", "system_prompt_suffix_fragments", "user_prompt_suffix_fragments"} and isinstance(value, nh.Extend)
            ):
                continue
            with pytest.raises(TypeError), nh.scope(**{field: value}):  # type: ignore[arg-type]
                pass
            assert get_scope().execution_reference is identity
            assert get_scope().step_executor is executor
            assert get_scope().usage_meter is meter


def test_mixed_composition_and_declaration_identity() -> None:
    value = IdentityOnly()
    hooks = oversight.Oversight()
    with (
        nh.run(StubExecutor()),
        nh.scope(tools=[lookup], task_references={"value": value}, oversight=hooks, system_prompt_suffix_fragments=["parent"]),
    ):
        original_tool = get_model_scope().tools[0]
        with nh.scope(
            tools=nh.Extend([lookup, original_tool, search]),
            task_references=nh.Merge({"value": value}),
            system_prompt_suffix_fragments=nh.Extend(["child"]),
            user_prompt_suffix_fragments=[],
            oversight=None,
        ):
            assert get_model_scope().tools[0] is original_tool
            assert [tool.name for tool in get_model_scope().tools] == ["lookup", "search"]
            assert get_model_scope().system_prompt_suffix_fragments == ("parent", "child")
            assert get_scope().task_references["value"] is value
            assert get_scope().oversight is None
        assert get_scope().oversight is hooks
        with pytest.raises(ToolNameConflictError), nh.scope(tools=nh.Extend([Tool(lookup)])):
            pass
        with pytest.raises(NameConflictError, match="Conflict for task reference"), nh.scope(task_references=nh.Merge({"value": IdentityOnly()})):
            pass
        with nh.scope(tools=[], task_references={}, system_prompt_suffix_fragments=[]):
            assert get_model_scope().tools == ()
            assert get_scope().task_references == {}
            assert get_model_scope().system_prompt_suffix_fragments == ()
        assert get_model_scope().tools[0] is original_tool


def test_explicit_tool_duplicates_and_namespace_independence() -> None:
    tool = Tool(lookup)
    with nh.run(StubExecutor()), nh.scope(tools=[tool, tool], task_references={"lookup": object()}):
        assert get_model_scope().tools == (tool,)
        with pytest.raises(ToolDeclarationError), nh.scope(tools=nh.Extend([lookup])):
            pass
        with pytest.raises(NameConflictError), nh.scope(tools=[Tool(lookup, name="nh_evaluate")]):
            pass
    assert issubclass(ToolNameConflictError, ToolDeclarationError)
    assert issubclass(ToolNameConflictError, NameConflictError)


def test_async_scopes_restore_and_isolate() -> None:
    async def worker(label: str) -> None:
        with nh.scope(system_prompt_suffix_fragments=[label]):
            await asyncio.sleep(0)
            assert get_model_scope().system_prompt_suffix_fragments == (label,)
            with pytest.raises(RuntimeError), nh.scope(system_prompt_suffix_fragments=[]):
                raise RuntimeError("child")
            assert get_model_scope().system_prompt_suffix_fragments == (label,)

    async def execute() -> None:
        with nh.run(StubExecutor()):
            await asyncio.gather(worker("first"), worker("second"))
            assert get_model_scope().system_prompt_suffix_fragments == ()

    asyncio.run(execute())


def test_required_getter_lifetimes_and_meter_none() -> None:
    for getter in (get_scope, get_step_context):
        with pytest.raises(nh.NighthawkError):
            getter()
    with pytest.raises(TypeError), nh.run(StubExecutor(), usage_meter=None):  # type: ignore[arg-type]
        pass
    with nh.run(StubExecutor()):
        with pytest.raises(nh.NighthawkError):
            get_step_context()
        assert get_scope().oversight is None


def test_task_references_are_available_through_the_active_environment() -> None:
    injected = object()
    seen: list[object] = []
    python_globals = dict(globals())

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            environment = request.step_context.environment
            assert environment.resolve("len") is len
            assert environment.resolve("len", source="task") is injected
            assert environment.task_name_to_value == {"len": injected}
            seen.append(environment.task_name_to_value["len"])
            with pytest.raises(TypeError):
                environment.task_name_to_value["len"] = None  # type: ignore[index]
            return StepCandidate(outcome=Pass())

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


def test_nested_runs_with_the_same_host_identifier_reset_model_presentation() -> None:
    with nh.run(StubExecutor(), run_id="caller-selected"), nh.scope(tools=[lookup]):
        assert get_model_scope().tools
        with nh.run(StubExecutor(), run_id="caller-selected"):
            assert get_model_scope().tools == ()
        assert get_model_scope().tools
