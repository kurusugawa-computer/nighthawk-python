"""Keyboard metadata and typed forms; palette text is never evaluated as Python."""

from __future__ import annotations

import dataclasses
import inspect
from dataclasses import dataclass
from typing import Annotated, Any, get_args, get_type_hints

from pydantic import Field, TypeAdapter

from nighthawk.parts.evidence import EvidenceBounds, EvidenceQuery
from nighthawk.session import HistoryFilter, LocalQuery, OperationDescriptor, OperatorRequest


@dataclass(frozen=True)
class KeyBinding:
    action: str
    keys: tuple[str, ...]
    label: str
    hint: str


KEY_BINDING_TUPLE = (
    KeyBinding("submit", ("escape", "enter"), "Escape-Enter", "submit"),
    KeyBinding("literal", ("escape", "n"), "Escape-N", "literal Natural"),
    KeyBinding("history", ("c-r",), "Ctrl+R", "input history"),
    KeyBinding("palette", ("c-p",), "Ctrl+P", "operations"),
    KeyBinding("inspect", ("c-t",), "Ctrl+T", "history inspector"),
    KeyBinding("help", ("f1",), "F1", "help"),
    KeyBinding("interrupt", ("c-c",), "Ctrl+C", "clear / interrupt"),
    KeyBinding("exit", ("c-d",), "Ctrl+D", "exit empty draft"),
    KeyBinding("latest", ("c-l",), "Ctrl+L", "follow latest"),
    KeyBinding("runs", ("f2",), "F2", "related runs / evidence"),
    KeyBinding("streams", ("f3",), "F3", "captured streams"),
    KeyBinding("filters", ("f4",), "F4", "history filters"),
    KeyBinding("next_page", ("f5",), "F5", "next observation page"),
    KeyBinding("background_interrupt", ("f6",), "F6", "interrupt selected background work"),
    KeyBinding("close_diagnostics", ("f10",), "F10", "close diagnostics after worker exit"),
    KeyBinding("scroll", ("pageup",), "PageUp", "pause / scroll output"),
    KeyBinding("scroll_down", ("pagedown",), "PageDown", "scroll output"),
)


def terminal_help() -> str:
    return (
        "Terminal input\n"
        "Natural language is the default (#>); Python uses !>. Prompts are not part of the submitted text.\n"
        "In an empty Natural draft, type ! then Space to switch to Python.\n"
        "Backspace on an already empty draft returns to Natural. Modes persist after submission.\n"
        "# stays ordinary text, including Python comments. Pasting never switches modes.\n"
        "Enter inserts a newline. Escape-Enter submits; Escape-N submits literal Natural language.\n"
        "To exit, switch to Python and submit session.exit() with Escape-Enter, or use Ctrl+D on an empty draft.\n"
        "A refused exit reports managed work that must cease before a fresh exit request.\n\n"
        "Mouse and clipboard\n"
        "Drag to select text; use the terminal's normal copy and paste commands.\n"
        "In VS Code on macOS, use Command-C to copy and Command-V to paste.\n"
        "The mouse wheel scrolls native terminal history. The application does not capture the mouse.\n"
        "Pasted text stays editable; it is never submitted by pasting.\n"
        "PageUp opens retained output for keyboard browsing; Escape or Ctrl+L returns to input.\n\n"
        "Keyboard operations\n"
        + "\n".join(f"{binding.label}: {binding.hint}" for binding in KEY_BINDING_TUPLE)
        + "\n\nInside an inspector: Up/Down selects, Enter opens or activates, Escape/Ctrl+C returns.\n"
        "Inside a form: Enter or Tab accepts a field; the final Enter activates the operation."
    )


@dataclass(frozen=True)
class ParameterField:
    path: tuple[str, ...]
    label: str
    value_type: str = "str"
    default: str = ""


_REQUEST_ADAPTER: TypeAdapter[OperatorRequest] = TypeAdapter(Annotated[OperatorRequest, Field(discriminator="operation")])
_OPERATION_NAME_TO_REQUEST_TYPE: dict[object, type] = {
    field.default: request_type
    for request_type in get_args(OperatorRequest.__value__)
    for field in dataclasses.fields(request_type)
    if field.name == "operation"
}


def operation_fields(descriptor: OperationDescriptor) -> tuple[ParameterField, ...]:
    """Derive form fields from the typed request that dispatches the operation."""
    request_type = _OPERATION_NAME_TO_REQUEST_TYPE[descriptor.name]
    field_name_to_type = get_type_hints(request_type)
    result: list[ParameterField] = []
    for parameter in dataclasses.fields(request_type):
        value_type = field_name_to_type[parameter.name]
        if parameter.name == "operation":
            continue
        if parameter.name == "binding_name":
            result.append(ParameterField(("binding_name",), "Existing trusted owner adapter binding"))
        elif value_type is LocalQuery:
            result.extend(
                (
                    ParameterField(("query", "name"), "Name contains"),
                    ParameterField(("query", "type_description"), "Type contains"),
                    ParameterField(("query", "exposure"), "Exposure (candidate/private/controller/unavailable)", "optional"),
                    ParameterField(("query", "starting_index"), "Starting item", "int", "0"),
                    ParameterField(("query", "maximum_item_count"), "Maximum items", "int", "20"),
                    ParameterField(("query", "maximum_character_count"), "Maximum characters", "int", "6000"),
                    ParameterField(("query", "revision"), "Observation revision", "int | None"),
                )
            )
        elif value_type is HistoryFilter:
            result.extend(
                ParameterField(("filters", name), label, "optional")
                for name, label in (
                    ("kind", "Record kind"),
                    ("cell_id", "Cell Id"),
                    ("run_id", "Run Id"),
                    ("outcome", "Cell outcome (success/failure/cancelled)"),
                )
            )
        elif value_type is EvidenceQuery:
            result.extend(
                (
                    ParameterField(("query", "starting_sequence"), "Starting sequence", "int", "0"),
                    ParameterField(("query", "event_kind_tuple"), "Event kinds (comma separated)", "tuple"),
                    ParameterField(("query", "attribution_node_id"), "Attribution node Id", "optional"),
                )
            )
        elif value_type is EvidenceBounds:
            result.extend(
                ParameterField(("bounds", name), name.replace("_", " "), "int", default)
                for name, default in (("maximum_event_count", "20"), ("maximum_character_count", "6000"), ("maximum_excerpt_character_count", "1000"))
            )
        else:
            default = "" if parameter.default in (dataclasses.MISSING, None) else str(parameter.default)
            result.append(ParameterField((parameter.name,), parameter.name.replace("_", " "), inspect.formatannotation(value_type), default))
    return tuple(result)


def typed_request(operation: str, fields: tuple[ParameterField, ...], values: tuple[str, ...]) -> OperatorRequest:
    argument_name_to_value: dict[str, Any] = {"operation": operation}
    for field, text in zip(fields, values, strict=True):
        if field.value_type == "int":
            value: Any = int(text or field.default)
        elif field.value_type == "tuple":
            value = tuple(item.strip() for item in text.split(",") if item.strip())
        elif field.value_type == "optional" or "None" in field.value_type:
            value = None if text == "" else int(text) if field.value_type.startswith("int") else text
        else:
            value = text
        destination = argument_name_to_value
        for component in field.path[:-1]:
            destination = destination.setdefault(component, {})
        destination[field.path[-1]] = value
    return _REQUEST_ADAPTER.validate_python(argument_name_to_value)
