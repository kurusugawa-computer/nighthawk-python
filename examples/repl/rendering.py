"""Append-only terminal presentation; stream records are separate from run evidence."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TextIO

from pydantic import BaseModel
from rich.console import Console
from rich.text import Text

from examples.repl.protocol import OutputRecord
from nighthawk.session import CellOutput
from nighthawk.session.formatting import describe_operation
from nighthawk.session.operator_contract import OperationDescriptor


@dataclass
class CompactConsole:
    """Local display numbers disambiguate interleaving; records keep full identities."""

    cell_id_to_number: dict[str, int] = field(default_factory=dict)
    run_id_to_number: dict[str, int] = field(default_factory=dict)
    cell_id_to_run_id_set: dict[str, set[str]] = field(default_factory=dict)
    finished_cell_id_set: set[str] = field(default_factory=set)
    last_cell_id: str | None = None
    mixed_cell_id_set: set[str] = field(default_factory=set)

    def render(self, record: OutputRecord) -> str:
        cell_id, run_id = record.cell_id, record.run_id
        label = record.semantic_kind or "output"
        if cell_id is not None:
            known = cell_id in self.cell_id_to_number
            self.cell_id_to_number.setdefault(cell_id, len(self.cell_id_to_number) + 1)
            if self.last_cell_id != cell_id and (known or self.last_cell_id is not None and self.last_cell_id not in self.finished_cell_id_set):
                self.mixed_cell_id_set.add(cell_id)
                if self.last_cell_id is not None:
                    self.mixed_cell_id_set.add(self.last_cell_id)
            if run_id is not None:
                self.run_id_to_number.setdefault(run_id, len(self.run_id_to_number) + 1)
                self.cell_id_to_run_id_set.setdefault(cell_id, set()).add(run_id)
            if cell_id in self.mixed_cell_id_set:
                label += f" cell {self.cell_id_to_number[cell_id]}"
            if run_id is not None and len(self.cell_id_to_run_id_set[cell_id]) > 1:
                label += f" run {self.run_id_to_number[run_id]}"
            self.last_cell_id = cell_id
            if record.semantic_kind == "status":
                self.finished_cell_id_set.add(cell_id)
        else:
            label += " unattributed"
        return f"[{label}] {record.text}" + (" [incomplete]" if record.incomplete else "") + "\n"


@dataclass(frozen=True)
class TerminalStreamRecord:
    cell_id: str
    stream: Literal["stdout", "stderr"]
    text: str
    incomplete: bool = False


def render_output(output: CellOutput, stream: TextIO) -> None:
    identity = f"cell={output.cell_id}"
    if output.run_id is not None:
        identity += f" run={output.run_id}"
    if output.event_id is not None:
        identity += f" event={output.event_id}"
    text = f"[{output.kind} {identity}] {output.text}"
    if output.incomplete:
        text += " [incomplete]"
    for reference in output.evidence_reference_tuple:
        text += f"\n[citation run={reference.run_id} event={reference.event_id} path={reference.value_path_tuple}]"
    kind_to_style = {"error": "red", "failure": "red", "status": "dim", "execution": "dim", "publication": "green", "answer": "cyan"}
    Console(file=stream, highlight=False).print(Text(text, style=kind_to_style.get(output.kind, "")), soft_wrap=True)


class CapturedStream(io.TextIOBase):
    encoding = "utf-8"

    def __init__(
        self,
        *,
        cell_id: str,
        stream: Literal["stdout", "stderr"],
        receive: Callable[[TerminalStreamRecord], None],
        destination: TextIO,
    ) -> None:
        self._cell_id = cell_id
        self._stream: Literal["stdout", "stderr"] = stream
        self._receive = receive
        self._destination = destination

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        # Chunk long writes without retaining arbitrary output objects.
        for i in range(0, len(text), 6000):
            chunk = text[i : i + 6000]
            self._receive(TerminalStreamRecord(self._cell_id, self._stream, chunk))
            self._destination.write(chunk)
        self._destination.flush()
        return len(text)

    def flush(self) -> None:
        self._destination.flush()


def interactive_text(text: str) -> str:
    """Escape controls before handing text to the screen layout, retaining line breaks."""

    return "".join(
        character if character in "\n\t" or ord(character) >= 32 and not 127 <= ord(character) <= 159 else f"\\x{ord(character):02x}"
        for character in text
    )


def semantic_text(record: object) -> str:
    """Original retained semantic text, without styled wrappers or visual line wrapping."""

    from examples.repl.protocol import OutputRecord

    if isinstance(record, (CellOutput, OutputRecord)):
        return record.text
    from nighthawk.session import HistoryRecord, PlainTextObservation

    if isinstance(record, (HistoryRecord, PlainTextObservation)):
        return record.text
    raise TypeError("No retained semantic text for this record")


def describe_result(result: object) -> str:
    """Describe typed operator values; arbitrary local objects never reach this function."""

    from dataclasses import fields, is_dataclass

    from nighthawk.session import LocalPage, PlainTextObservation

    if type(result) is OperationDescriptor:
        return describe_operation(result)
    if isinstance(result, PlainTextObservation):
        return result.text
    if isinstance(result, LocalPage):
        heading = f"Locals observed at {result.observed_at:.3f}, revision {result.revision}"
        if result.session_name_shadowed:
            heading += "\nThe Python name session is shadowed; canonical palette operations remain available."
        rows = [f"{item.name}: {item.type_description} = {item.preview}\n  {item.exposure_description}" for item in result.binding_tuple]
        if result.unavailable_reason:
            rows.append(result.unavailable_reason)
        if result.incomplete:
            rows.append("[Incomplete observation]")
        if result.next_starting_index is not None:
            rows.append(f"Next starting index: {result.next_starting_index}")
        return heading + "\n\n" + "\n\n".join(rows)
    if is_dataclass(result) and not isinstance(result, type):
        return "\n".join(f"{field.name.replace('_', ' ')}: {getattr(result, field.name)}" for field in fields(result))
    if isinstance(result, tuple):
        return "\n\n".join(describe_result(item) for item in result) or "No observations"
    if isinstance(result, BaseModel):
        return result.model_dump_json(indent=2)
    return str(result)
