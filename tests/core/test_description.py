from __future__ import annotations

import functools
import inspect
from dataclasses import InitVar, dataclass, field
from typing import Annotated, ClassVar, NamedTuple

import pytest
from pydantic import BaseModel, field_serializer

from nighthawk.core.description import describe_callable, describe_public_members, discover_type_alias_reference_names, safe_preview
from nighthawk.core.inspection import describe_value
from tests.support.prompts import build_step_context, build_user_prompt_text


def test_callable_annotation_expressions_are_not_evaluated_for_presentation() -> None:
    effects: list[str] = []

    def annotation_effect() -> type:
        effects.append("annotation")
        return str

    def action(value: annotation_effect()) -> annotation_effect():  # type: ignore[valid-type]
        raise AssertionError("body must not run")

    context = build_step_context(python_globals={"annotation_effect": annotation_effect}, python_locals={"action": action})
    prompt = build_user_prompt_text(processed_natural_program="Describe <action>.", step_context=context)
    description = describe_value(action)
    assert "annotation_effect()" in prompt
    assert "annotation_effect()" in str(description["signature"])
    assert "not evaluated" in str(description["annotation_note"])
    assert effects == []


def test_callable_signature_property_is_not_executed() -> None:
    effects: list[str] = []

    class Action:
        @property
        def __signature__(self) -> object:
            effects.append("signature property")
            raise AssertionError("must not run")

        def __call__(self) -> None:
            effects.append("call")

    value = Action()
    description = describe_value(value)
    assert description["signature"] is None
    assert "unavailable" in str(description["metadata_omission"])
    context = build_step_context(python_globals={}, python_locals={"action": value})
    assert "signature unavailable" in build_user_prompt_text(processed_natural_program="Describe.", step_context=context)
    assert effects == []


def test_annotation_metadata_and_callable_defaults_do_not_invoke_repr() -> None:
    effects: list[str] = []

    class Metadata:
        def __repr__(self) -> str:
            effects.append("repr")
            raise AssertionError("must not run")

    def action(value: object = Metadata()) -> object:
        return value

    action.__annotations__ = {"value": Annotated[int, Metadata()], "return": object}
    signature = describe_callable(action).signature_text
    assert signature is not None
    assert "Annotated[int, <opaque Metadata>]" in signature
    assert "= <opaque Metadata>" in signature or "=<opaque Metadata>" in signature
    partial = functools.partial(action, value=Metadata())
    assert "<opaque Metadata>" in str(describe_value(partial)["signature"])
    assert effects == []


def test_variadic_tuple_annotation_is_spelled_as_python() -> None:
    def collect(values: tuple[int, ...]) -> tuple[str, ...]:
        return tuple(str(value) for value in values)

    assert describe_callable(collect).signature_text == "(values: tuple[int, ...]) -> tuple[str, ...]"


def test_custom_signature_parameter_methods_are_not_called() -> None:
    effects: list[str] = []

    class Parameter(inspect.Parameter):
        def replace(self, **changes: object) -> inspect.Parameter:
            effects.append("parameter method")
            raise AssertionError("must not run")

    def action() -> None:
        pass

    action.__signature__ = inspect.Signature([Parameter("value", inspect.Parameter.POSITIONAL_OR_KEYWORD)])  # type: ignore[attr-defined]
    result = describe_value(action)
    assert result["signature"] is None
    assert "unavailable" in str(result["metadata_omission"])
    assert effects == []


def test_pydantic_field_property_and_serializer_are_not_executed() -> None:
    effects: list[str] = []

    class Record(BaseModel):
        value: int

        @field_serializer("value")
        def serialize_value(self, value: int) -> str:
            effects.append("serializer")
            return str(value)

    record = Record(value=3)

    def read_property(self: Record) -> int:
        effects.append("property")
        return 5

    Record.value = property(read_property)  # type: ignore[assignment]
    members = describe_public_members(record)
    assert "value" not in members.field_name_to_value
    assert "value" in members.omitted_names
    context = build_step_context(python_globals={}, python_locals={"record": record, "nested": {"record": record}})
    prompt = build_user_prompt_text(processed_natural_program="Describe <record>.", step_context=context)
    assert "record.value:" not in prompt
    assert "<opaque Record>" in prompt
    assert effects == []


@pytest.mark.parametrize("surface", ["prompt", "inspection"])
def test_metadata_bypasses_instance_and_metaclass_attribute_hooks(surface: str) -> None:
    effects: list[str] = []

    class Metaclass(type):
        def __getattribute__(metaclass, name: str) -> object:  # pyright: ignore[reportSelfClsParameterName]  # noqa: N804
            effects.append(f"class:{name}")
            return super().__getattribute__(name)

        def __eq__(metaclass, other: object) -> bool:  # pyright: ignore[reportSelfClsParameterName]  # noqa: N804
            effects.append("class equality")
            return False

        __hash__ = type.__hash__

    class Opaque(metaclass=Metaclass):
        value = 3

        def __getattribute__(self, name: str) -> object:
            effects.append(f"instance:{name}")
            return super().__getattribute__(name)

    value = Opaque()
    if surface == "prompt":
        context = build_step_context(python_globals={}, python_locals={"value": value})
        effects.clear()
        result: object = build_user_prompt_text(processed_natural_program="Describe.", step_context=context)
    else:
        effects.clear()
        result = describe_value(value)
    assert "Opaque" in str(result)
    assert effects == []


def test_slot_replaced_by_property_is_omitted_without_execution() -> None:
    effects: list[str] = []

    @dataclass(slots=True)
    class Record:
        value: int

    record = Record(3)

    def read_property(self: Record) -> int:
        effects.append("property")
        return 5

    Record.value = property(read_property)  # type: ignore[assignment]
    assert describe_public_members(record).omitted_names == ("value",)
    assert effects == []


class _Location(NamedTuple):
    path: str
    line: int


@dataclass(slots=True)
class _Finding:
    severity: str
    location: _Location
    category: ClassVar[str] = "review"
    scale: InitVar[int] = 1
    note: str = field(default="", repr=False)

    def __post_init__(self, scale: int) -> None:
        del scale


class _Report(BaseModel):
    subject: str
    findings: tuple[_Finding, ...]


def test_nested_dataclass_and_model_previews_list_declared_fields_in_order() -> None:
    report = _Report(subject="billing", findings=(_Finding("blocking", _Location("a.py", 3)),))
    assert safe_preview({"report": report}, depth=5) == {
        "report": {"subject": "billing", "findings": [{"severity": "blocking", "location": {"path": "a.py", "line": 3}, "note": ""}]}
    }
    assert safe_preview([report], depth=3) == [{"subject": "billing", "findings": ["<_Finding; fields omitted>"]}]


def test_nested_instance_preview_snips_fields_beyond_the_item_limit() -> None:
    @dataclass
    class Wide:
        first: int
        second: int
        third: int

    assert safe_preview([Wide(1, 2, 3)], max_items=2) == [
        {"first": 1, "second": 2, "<snipped>": "More named fields are available through inspection or Python access"}
    ]


def test_nested_instance_preview_reads_storage_without_attribute_hooks() -> None:
    effects: list[str] = []

    @dataclass
    class Hooked:
        value: int

        def __getattribute__(self, name: str) -> object:
            effects.append(name)
            return super().__getattribute__(name)

    context = build_step_context(python_globals={}, python_locals={"holder": {"item": Hooked(3)}})
    effects.clear()
    prompt = build_user_prompt_text(processed_natural_program="Describe <holder>.", step_context=context)
    assert '"value":3' in prompt.replace(" ", "")
    assert effects == []


def test_nested_instance_with_descriptor_field_remains_opaque() -> None:
    effects: list[str] = []

    @dataclass
    class Record:
        value: int
        label: str

    record = Record(3, "kept")

    def read_property(self: Record) -> int:
        effects.append("property")
        return 5

    Record.value = property(read_property)  # type: ignore[assignment]
    assert safe_preview([record]) == ["<opaque Record>"]
    assert effects == []


def test_static_instance_field_and_class_shadowing_remain_correct() -> None:
    class Parent:
        def action(self) -> None:
            raise AssertionError("must not run")

    class Child(Parent):
        action = 5  # type: ignore[assignment]

    value = Child()
    value.other = lambda: None  # type: ignore[attr-defined]
    members = describe_public_members(value)
    assert members.field_name_to_value["action"] == 5
    assert "action" not in members.method_name_to_value
    assert describe_callable(members.method_name_to_value["other"]).signature_text == "()"


def test_lazy_type_alias_is_discovered_without_evaluating_its_body() -> None:
    effects: list[str] = []

    def annotation_effect() -> type:
        effects.append("alias body")
        return str

    type Alias = annotation_effect()  # type: ignore[valid-type]

    def action() -> list[Alias]:
        return []

    discovered = discover_type_alias_reference_names(step_locals={"action": action}, step_globals={"Alias": Alias}, input_binding_names=())
    assert discovered == frozenset({"Alias"})
    context = build_step_context(python_globals={"Alias": Alias}, python_locals={"action": action})
    prompt = build_user_prompt_text(processed_natural_program="Describe <Alias>.", step_context=context)
    assert "alias expansion omitted" in prompt
    assert effects == []


def test_known_value_inspection_does_not_infer_binding_provenance() -> None:
    value: list[str] = []
    result = describe_value(value)
    assert result["type"] == "list" and result["preview"] == []
    assert all(name not in result for name in ("origin", "source", "annotation", "output_contract", "write_eligible"))
