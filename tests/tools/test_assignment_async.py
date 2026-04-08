from __future__ import annotations

import asyncio
import dataclasses

import pytest
from pydantic import BaseModel

from nighthawk.runtime.step_context import StepContext
from nighthawk.tools.assignment import assign_tool, assign_tool_async, eval_expression_async
from nighthawk.tools.contracts import ToolBoundaryError


def _new_step_context() -> StepContext:
    return StepContext(
        step_id="test_assignment_async",
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        implicit_reference_name_to_value={},
    )


def test_eval_expression_async_supports_top_level_await() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = _new_step_context()
    step_context.step_locals["calculate"] = calculate

    result = asyncio.run(eval_expression_async(step_context, "await calculate(1, 2)"))
    assert result == 17


def test_eval_expression_async_awaits_returned_awaitable_implicitly() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = _new_step_context()
    step_context.step_locals["calculate"] = calculate

    result = asyncio.run(eval_expression_async(step_context, "calculate(1, 2)"))
    assert result == 17


def test_assign_tool_async_assigns_awaited_result() -> None:
    async def calculate(a: int, b: int) -> int:
        return a + b * 8

    step_context = _new_step_context()
    step_context.step_locals["calculate"] = calculate

    update = asyncio.run(assign_tool_async(step_context, "result", "await calculate(1, 2)"))

    assert update["updates"][0]["value"] == 17
    assert step_context.step_locals["result"] == 17


def test_assign_rejects_rebind_of_read_binding() -> None:
    step_context = StepContext(
        step_id="test_read_guard",
        step_globals={"__builtins__": __builtins__},
        step_locals={"data": {"key": "old"}},
        binding_commit_targets=set(),
        read_binding_names=frozenset({"data"}),
        implicit_reference_name_to_value={},
    )

    with pytest.raises(ToolBoundaryError):
        assign_tool(step_context, "data", "{'key': 'new'}")

    assert step_context.step_locals["data"] == {"key": "old"}


def test_assign_allows_rebind_when_both_read_and_write_binding() -> None:
    step_context = StepContext(
        step_id="test_read_write",
        step_globals={"__builtins__": __builtins__},
        step_locals={"data": {"key": "old"}},
        binding_commit_targets={"data"},
        read_binding_names=frozenset(),
        implicit_reference_name_to_value={},
    )

    result = assign_tool(step_context, "data", "{'key': 'new'}")

    assert result["updates"][0]["value"] == {"key": "new"}
    assert step_context.step_locals["data"] == {"key": "new"}
    assert "data" in step_context.assigned_binding_names


def test_assign_allows_multiple_rebinds_of_new_local() -> None:
    step_context = StepContext(
        step_id="test_new_local",
        step_globals={"__builtins__": __builtins__},
        step_locals={},
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        implicit_reference_name_to_value={},
    )

    assign_tool(step_context, "temp", "1")
    assert step_context.step_locals["temp"] == 1

    assign_tool(step_context, "temp", "2")
    assert step_context.step_locals["temp"] == 2


# -- Dotted-path type validation tests --


def test_assign_dotted_path_dataclass_known_field_valid() -> None:
    @dataclasses.dataclass
    class Config:
        name: str = "default"
        count: int = 0

    config = Config()
    step_context = _new_step_context()
    step_context.step_locals["config"] = config

    assign_tool(step_context, "config.name", "'updated'")
    assert config.name == "updated"


def test_assign_dotted_path_dataclass_known_field_invalid_type() -> None:
    @dataclasses.dataclass
    class Config:
        count: int = 0

    config = Config()
    step_context = _new_step_context()
    step_context.step_locals["config"] = config

    with pytest.raises(ToolBoundaryError) as exception_info:
        assign_tool(step_context, "config.count", "'not_an_int'")
    assert exception_info.value.kind == "invalid_input"
    assert config.count == 0


def test_assign_dotted_path_dataclass_unknown_field_allowed() -> None:
    @dataclasses.dataclass
    class Config:
        name: str = "default"

    config = Config()
    step_context = _new_step_context()
    step_context.step_locals["config"] = config

    assign_tool(step_context, "config.new_field", "42")
    assert config.new_field == 42  # type: ignore[attr-defined]


def test_assign_dotted_path_dataclass_type_coercion() -> None:
    @dataclasses.dataclass
    class Config:
        count: int = 0

    config = Config()
    step_context = _new_step_context()
    step_context.step_locals["config"] = config

    assign_tool(step_context, "config.count", "'1'")
    assert config.count == 1
    assert isinstance(config.count, int)


def test_assign_dotted_path_frozen_dataclass_rejects_assignment() -> None:
    @dataclasses.dataclass(frozen=True)
    class Immutable:
        value: int = 1

    instance = Immutable()
    step_context = _new_step_context()
    step_context.step_locals["instance"] = instance

    with pytest.raises(ToolBoundaryError) as exception_info:
        assign_tool(step_context, "instance.value", "2")
    assert exception_info.value.kind == "resolution"


def test_assign_dotted_path_plain_class_known_field_valid() -> None:
    class Settings:
        host: str
        port: int

        def __init__(self) -> None:
            self.host = "localhost"
            self.port = 8080

    settings = Settings()
    step_context = _new_step_context()
    step_context.step_locals["settings"] = settings

    assign_tool(step_context, "settings.port", "9090")
    assert settings.port == 9090


def test_assign_dotted_path_plain_class_known_field_invalid_type() -> None:
    class Settings:
        port: int

        def __init__(self) -> None:
            self.port = 8080

    settings = Settings()
    step_context = _new_step_context()
    step_context.step_locals["settings"] = settings

    with pytest.raises(ToolBoundaryError) as exception_info:
        assign_tool(step_context, "settings.port", "'not_a_port'")
    assert exception_info.value.kind == "invalid_input"
    assert settings.port == 8080


def test_assign_dotted_path_plain_class_unknown_field_allowed() -> None:
    class Container:
        x: int = 1

    container = Container()
    step_context = _new_step_context()
    step_context.step_locals["container"] = container

    assign_tool(step_context, "container.y", "'hello'")
    assert container.y == "hello"  # type: ignore[attr-defined]


def test_assign_dotted_path_slots_class_unknown_field_rejected() -> None:
    class Slotted:
        __slots__ = ("x",)
        x: int

        def __init__(self) -> None:
            self.x = 1

    instance = Slotted()
    step_context = _new_step_context()
    step_context.step_locals["instance"] = instance

    with pytest.raises(ToolBoundaryError) as exception_info:
        assign_tool(step_context, "instance.y", "2")
    assert exception_info.value.kind == "resolution"


def test_assign_dotted_path_pydantic_unknown_field_rejected() -> None:
    class MyModel(BaseModel):
        name: str = "default"

    model = MyModel()
    step_context = _new_step_context()
    step_context.step_locals["model"] = model

    with pytest.raises(ToolBoundaryError) as exception_info:
        assign_tool(step_context, "model.unknown", "'value'")
    assert exception_info.value.kind == "invalid_input"


def test_assign_dotted_path_no_annotations_allowed() -> None:
    class Bare:
        def __init__(self) -> None:
            self.x = 1

    instance = Bare()
    step_context = _new_step_context()
    step_context.step_locals["instance"] = instance

    assign_tool(step_context, "instance.x", "99")
    assert instance.x == 99


def test_assign_dotted_path_marks_write_binding_root_dirty() -> None:
    class Model(BaseModel):
        value: int = 0

    step_context = StepContext(
        step_id="test_write_root_dirty",
        step_globals={"__builtins__": __builtins__},
        step_locals={"model": Model()},
        binding_commit_targets={"model"},
        read_binding_names=frozenset(),
        implicit_reference_name_to_value={},
    )

    assign_tool(step_context, "model.value", "2")

    assert "model" in step_context.dirty_output_binding_names
    assert "model" not in step_context.assigned_binding_names


def test_assign_dotted_path_read_binding_root_is_not_marked_dirty() -> None:
    class Model(BaseModel):
        value: int = 0

    step_context = StepContext(
        step_id="test_read_root_not_dirty",
        step_globals={"__builtins__": __builtins__},
        step_locals={"model": Model()},
        binding_commit_targets=set(),
        read_binding_names=frozenset({"model"}),
        implicit_reference_name_to_value={},
    )

    assign_tool(step_context, "model.value", "2")

    assert step_context.dirty_output_binding_names == set()
