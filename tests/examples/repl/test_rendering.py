"""Semantic text remains separate from terminal presentation controls."""

import asyncio
import io
from contextlib import asynccontextmanager

import pyte
import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from examples.repl.application import TerminalApplication
from examples.repl.input import Draft
from examples.repl.protocol import Observation, OutputRecord
from examples.repl.rendering import interactive_text, semantic_text
from nighthawk.agent.session import ConversationRecord, ExecutionObservation

from .helpers import ApplicationClient, until


def test_plain_text_view_has_no_presentation_artifacts() -> None:
    record = ConversationRecord(1, "cell", "input", "def sample():\n    return '日本語'\n")
    assert semantic_text(record) == "def sample():\n    return '日本語'\n"
    assert interactive_text("before\x1b[2Jafter\r\x00\n") == "before\\x1b[2Jafter\\x0d\\x00\n"


class DecodedOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.screen = pyte.Screen(100, 16)
        self.stream = pyte.Stream(self.screen)

    def write(self, text: str) -> int:
        self.stream.feed(text)
        return super().write(text)

    @property
    def visible(self) -> str:
        return "\n".join(self.screen.display)

    @property
    def status_line(self) -> str:
        return next((line for line in reversed(self.screen.display) if line.strip()), "")


@asynccontextmanager
async def rendered_terminal():
    client = ApplicationClient()
    capture = DecodedOutput()
    output = Vt100_Output(capture, lambda: Size(rows=capture.screen.lines, columns=capture.screen.columns), enable_cpr=False)
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=output, no_animation=True, clock=lambda: 12)
        task = asyncio.create_task(interface.application.run_async())
        try:
            await until(lambda: interface.application.is_running)
            yield interface, capture
        finally:
            if interface.application.is_running:
                interface.application.exit()
            await task
            client.session.close()


async def redraw(interface: TerminalApplication) -> None:
    rendered = asyncio.Event()

    def on_render(application) -> None:
        rendered.set()

    interface.application.after_render += on_render
    try:
        interface.application.invalidate()
        await asyncio.wait_for(rendered.wait(), 5)
    finally:
        interface.application.after_render -= on_render


@pytest.mark.parametrize("view", ["history", "streams", "input_history"])
@pytest.mark.parametrize("preceding", ["FIRST\n" + "line\n" * 35, "日本語e\u0301" * 55])
async def test_selected_record_remains_visible_after_long_records_and_resize(view: str, preceding: str) -> None:
    async with rendered_terminal() as (interface, capture):
        if view == "history":
            interface.state.record_index_to_record.update(
                {0: ConversationRecord(0, "old", "input", preceding), 1: ConversationRecord(1, "new", "input", "SELECTED_MARKER")}
            )
        elif view == "streams":
            interface.state.output_record_list.extend(
                (
                    OutputRecord(sequence=1, cell_id="old", category="stdout", text=preceding),
                    OutputRecord(sequence=2, cell_id="new", category="stdout", text="SELECTED_MARKER"),
                )
            )
        else:
            interface.state.input_history.add(Draft("SELECTED_MARKER"))
            interface.state.input_history.add(Draft(preceding))
        interface._open(view)
        interface._move(1)
        await redraw(interface)
        assert "SELECTED_MARKER" in capture.visible
        assert ("> 1:" if view != "input_history" else "> SELECTED_MARKER") in capture.visible
        for columns in (40, 100):
            capture.screen.resize(16, columns)
            await redraw(interface)
            assert "SELECTED_MARKER" in capture.visible
            assert interface.application.layout.has_focus(interface.search)
        if view == "history":
            interface.state.record_index_to_record[2] = ConversationRecord(2, "later", "answer", preceding)
            await redraw(interface)
            assert "SELECTED_MARKER" in capture.visible
            assert interface.inspector.selected_record_index == 1
        interface._activate()
        await redraw(interface)
        assert "SELECTED_MARKER" in capture.visible
        if view == "input_history":
            assert interface.editor.text == "SELECTED_MARKER"
        else:
            assert interface.detail.text == "SELECTED_MARKER"
            interface._back()
            await redraw(interface)
            assert "SELECTED_MARKER" in capture.visible and "Search:" in capture.visible


@pytest.mark.parametrize("view", ["main", "history"])
async def test_one_status_line_keeps_pending_state_and_elapsed_without_operation_hints(view: str) -> None:
    async with rendered_terminal() as (interface, capture):
        interface.state.receive(
            Observation(
                revision=1,
                observed_at=10,
                cell_id="cell",
                state="interruption_requested",
                started_at=10,
                execution=ExecutionObservation("cell", (), "interruption_requested", "Running Python", 10, 10, 1, True),
            )
        )
        if view != "main":
            interface._open(view)
        capture.screen.resize(16, 40)
        await redraw(interface)
        assert "Interrupt pending" in capture.status_line and "2.0s" in capture.status_line
        assert all(key not in capture.visible for key in ("Ctrl+C", "Esc back", "Escape-Enter", "Up/Down"))
        assert sum("Interrupt pending" in line for line in capture.screen.display) == 1
        interface.clock = lambda: 13
        await redraw(interface)
        assert "3.0s" in capture.status_line
        capture.screen.resize(16, 16)
        await redraw(interface)
        assert capture.status_line.strip() == "Pending"
        capture.screen.resize(16, 100)
        await redraw(interface)
        assert "Interruption requested; cessation unconfirmed" in capture.status_line


async def test_footer_has_one_line_and_terminal_instructions_appear_on_request() -> None:
    async with rendered_terminal() as (interface, capture):
        await redraw(interface)
        lines = [line.strip() for line in capture.screen.display if line.strip()]
        assert lines == ["#>", "Starting | observation unavailable"]
        interface._action("help")
        await redraw(interface)
        assert "Escape-Enter" in interface.detail.text
        assert "session.exit()" in interface.detail.text
        assert "Command-C" in interface.detail.text and "Command-V" in interface.detail.text
        assert "native terminal history" in interface.detail.text
        assert "\x1b[?1049h" not in capture.getvalue()
        assert "\x1b[?1000h" not in capture.getvalue()


def test_console_output_preserves_partial_lines_and_labels_bounded_delivery() -> None:
    from examples.repl.state import ScreenState

    state = ScreenState()
    state.append("  first")
    assert state.take_console_text() == ""
    state.append(" line\n    next")
    assert state.take_console_text() == "  first line\n"
    assert state.pending_console_text == "    next"
    assert state.take_console_text(final=True) == "    next\n"
    state.append("x" * 64000)
    assert len(state.pending_console_text) < 32000
    assert "delivery incomplete" in state.take_console_text()
    assert state.transcript.endswith("x" * 64000)


async def test_idle_status_is_stable_and_has_no_empty_fields() -> None:
    async with rendered_terminal() as (interface, capture):
        interface.state.receive(Observation(revision=1, observed_at=10, state="success"))
        await redraw(interface)
        assert capture.status_line.strip() == "Idle (success)"
        interface.clock = lambda: 500
        await redraw(interface)
        assert capture.status_line.strip() == "Idle (success)"


def test_compact_output_retains_identity_and_disambiguates_interleaved_sources() -> None:
    client = ApplicationClient()
    interface = TerminalApplication(client)

    def record(sequence: int, cell: str, run: str | None, kind: str = "answer") -> OutputRecord:
        return OutputRecord(sequence=sequence, cell_id=cell, run_id=run, category="semantic", semantic_kind=kind, text="retained text")

    records = [
        record(1, "first-cell", "first-run"),
        record(2, "first-cell", "second-run"),
        record(3, "second-cell", "third-run"),
        record(4, "first-cell", "first-run"),
    ]
    try:
        for item in records:
            interface._output(item)
        assert interface.state.transcript.startswith("[answer] retained text\n")
        assert "[answer run 2]" in interface.state.transcript
        assert "[answer cell 2]" in interface.state.transcript and "[answer cell 1 run 1]" in interface.state.transcript
        assert "first-cell" not in interface.state.transcript and "first-run" not in interface.state.transcript
        assert interface.state.output_record_list == records
        interface.state.record_index_to_record[0] = ConversationRecord(0, "first-cell", "answer", "retained text", "first-run")
        interface._open("history")
        text = "".join(fragment[1] for fragment in interface._rows_text())
        assert "cell=first-cell (cell 1) run=first-run" in text
        interface._run_id_tuple = ("first-run", "second-run")
        interface._open("runs")
        assert "run 2: second-run" in "".join(fragment[1] for fragment in interface._rows_text())
    finally:
        client.session.close()
