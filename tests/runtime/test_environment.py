from __future__ import annotations

import asyncio
from types import CellType

import pytest

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepRequest, assign_expression_async, evaluate_expression_async
from nighthawk.runtime.environment import StepEnvironment


def test_precedence_and_explicit_source_selection() -> None:
    environment = StepEnvironment(
        python_name_to_value={"i": 99, "present": None},
        python_globals={"global_value": 99},
        task_name_to_value={"i": 8, "present": 8, "global_value": 8, "len": 8, "injected": 8},
        output_name_set=frozenset({"i"}),
    )
    assert environment.resolve("i") == 99
    assert environment.resolve("i", source="task") == 8
    assert environment.resolve("present") is None
    assert environment.resolve("global_value") == 99
    assert environment.resolve("len") is len
    assert environment.resolve("injected") == 8
    environment.assign("i", 100)
    assert environment.resolve("i") == 100
    assert environment.resolve("i", source="python") == 99
    assert environment.describe("i")["shadowed_sources"] == ["python", "task"]


def test_unknown_source_is_rejected() -> None:
    environment = StepEnvironment(python_name_to_value={"value": 1}, python_globals={})
    with pytest.raises(ValueError, match="Unknown environment source"):
        environment.resolve("value", source="unknown")  # type: ignore[arg-type]
    assert "error" in environment.discover(source="unknown")  # type: ignore[arg-type]


def test_unbound_local_and_cell_stop_fallback() -> None:
    environment = StepEnvironment(
        python_name_to_value={},
        python_globals={"local": 1, "cell": 1},
        local_name_set=frozenset({"local"}),
        python_name_to_cell={"cell": CellType()},
        task_name_to_value={"local": 3, "cell": 3},
    )
    for name in ("local", "cell"):
        with pytest.raises(UnboundLocalError):
            environment.resolve(name)
        with pytest.raises(UnboundLocalError):
            assert name in environment.expression_namespace()
        assert environment.describe(name)["unbound"] is True


def test_module_and_closure_rebinding_remain_live() -> None:
    python_globals: dict[str, object] = {"global_value": 1}
    cell = CellType(2)
    environment = StepEnvironment(python_name_to_value={"cell": 2}, python_globals=python_globals, python_name_to_cell={"cell": cell})
    python_globals["global_value"] = 3
    cell.cell_contents = 4
    assert environment.resolve("global_value") == 3
    assert environment.resolve("cell") == 4


def test_explicit_working_snapshot_preserves_associations_and_shares_objects() -> None:
    shared: list[int] = []
    parent = StepEnvironment(
        python_name_to_value={"shared": shared, "__nh_execution__": object()},
        python_globals={"global_value": 1},
        task_name_to_value={"injected": 1},
        runtime_name_set=frozenset({"__nh_execution__"}),
    )
    parent.assign("i", 5)
    child = StepEnvironment(python_name_to_value={}, python_globals={}, task_name_to_value=parent.snapshot_working_state())
    parent.assign("i", 8)
    shared.append(1)
    assert child.resolve("i") == 5
    assert child.resolve("shared") is shared
    assert shared == [1]
    for name in ("global_value", "injected", "len", "__nh_execution__"):
        assert name not in child.task_name_to_value
    child.assign("i", 9)
    assert child.resolve("i") == 9
    assert parent.resolve("i") == 8


def test_views_are_shallow_read_only() -> None:
    environment = StepEnvironment(python_name_to_value={"value": 1}, python_globals={})
    for view in (
        environment.python_name_to_value,
        environment.task_name_to_value,
        environment.step_name_to_value,
    ):
        with pytest.raises(TypeError):
            view["value"] = 2  # type: ignore[index]


@pytest.mark.parametrize(
    "expression, expected", [("(lambda: value)()", 7), ("list(value + i for i in range(2))", [7, 8]), ("(lambda value: value)(9)", 9)]
)
def test_nested_expression_scopes_use_effective_lookup(expression: str, expected: object) -> None:
    environment = StepEnvironment(python_name_to_value={"value": 7}, python_globals={})
    namespace = environment.expression_namespace()
    assert eval(expression, namespace, namespace) == expected


def test_expression_namespace_membership_follows_live_lookup() -> None:
    python_globals: dict[str, object] = {"global_value": 1}
    environment = StepEnvironment(
        python_name_to_value={"local": None, "hidden": 1},
        python_globals=python_globals,
        python_name_to_cell={"cell": CellType(2)},
        task_name_to_value={"injected": 4},
        runtime_name_set=frozenset({"hidden"}),
    )
    namespace = environment.expression_namespace({"override": None})
    for name in ("local", "global_value", "cell", "injected", "override", "len", "__builtins__", environment.runtime_reference):
        assert name in namespace
    assert "missing" not in namespace
    assert "hidden" not in namespace
    assert 42 not in namespace

    python_globals["later"] = 5
    environment.assign("assigned", 6)
    assert "later" in namespace and namespace["later"] == 5
    assert "assigned" in namespace and namespace["assigned"] == 6


def test_existing_python_names_require_output_declaration() -> None:
    environment = StepEnvironment(python_name_to_value={"value": 7}, python_globals={"global_value": 1})
    for name in ("value", "global_value", "len", environment.runtime_reference):
        with pytest.raises(ValueError, match="Cannot rebind"):
            environment.assign(name, 9)


def test_nested_natural_counter_proof() -> None:
    child_parent_execution_id_list: list[str] = []
    parent_execution_id_list: list[str] = []

    @nh.function
    async def block_two() -> str:
        message = ""
        """execute
        Inspect <i>. Set <:message> to fifth on iteration 5, eighth on 8, and ordinary otherwise.
        """
        return message

    @nh.function
    async def block_one() -> list[str]:
        nonlocal block_two
        messages: list[str] = []
        """execute
        Call <block_two> ten times with explicitly shared iteration context and collect <:messages>.
        """
        return messages

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            context = request.step_context
            if "message" in list(request.output_binding_names):
                assert context.execution_reference.parent_step_execution_id is not None
                child_parent_execution_id_list.append(context.execution_reference.parent_step_execution_id)
                iteration = context.environment.resolve("i")
                assert isinstance(iteration, int)
                return StepCandidate(outcome=Pass(), binding_name_to_value={"message": {5: "fifth", 8: "eighth"}.get(iteration, "ordinary")})
            messages: list[object] = []
            assert context.execution_reference.step_execution_id is not None
            parent_execution_id_list.append(context.execution_reference.step_execution_id)
            for i in range(1, 11):
                await assign_expression_async(context, "i", str(i))
                with nh.scope(task_references={"i": context.environment.resolve("i")}):
                    messages.append(await evaluate_expression_async(context, "block_two()"))
            return StepCandidate(outcome=Pass(), binding_name_to_value={"messages": messages})

    with nh.run(Executor()):
        assert asyncio.run(block_one()) == ["ordinary"] * 4 + ["fifth", "ordinary", "ordinary", "eighth", "ordinary", "ordinary"]
    assert len(parent_execution_id_list) == 1
    assert child_parent_execution_id_list == parent_execution_id_list * 10


@pytest.mark.parametrize("name", ["__nh_application", "__nighthawk_application", "__nh_execution__"])
@pytest.mark.parametrize("source", ["local", "global", "cell"])
def test_application_prefix_keeps_lexical_priority_and_qualified_access(name: str, source: str) -> None:
    environment = StepEnvironment(
        python_name_to_value={name: None} if source == "local" else {},
        python_globals={name: None} if source == "global" else {},
        python_name_to_cell={name: CellType(None)} if source == "cell" else {},
        task_name_to_value={name: 9},
        output_name_set=frozenset({name}),
    )
    assert environment.resolve(name) is None
    assert environment.describe(name)["origin"] == "python"
    assert name in environment.python_name_to_value
    namespace = environment.expression_namespace()
    assert eval(environment.reference(name, source="python"), namespace, namespace) is None
    assert environment.resolve(name, source="task") == 9
    environment.assign(name, 7)
    assert environment.resolve(name) == 7
    assert environment.resolve(name, source="python") is None
    assert environment.snapshot_working_state()[name] == 7


@pytest.mark.parametrize("cell", [False, True])
def test_unbound_application_prefix_blocks_fallback(cell: bool) -> None:
    name = "__nh_execution__"
    environment = StepEnvironment(
        python_name_to_value={},
        python_globals={name: 99},
        local_name_set=frozenset() if cell else frozenset({name}),
        python_name_to_cell={name: CellType()} if cell else {},
        task_name_to_value={name: 9},
    )
    with pytest.raises(UnboundLocalError):
        environment.resolve(name)
    assert environment.describe(name)["unbound"] is True
    assert name not in environment.snapshot_working_state()
