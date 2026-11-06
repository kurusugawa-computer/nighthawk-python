import asyncio
from contextlib import contextmanager

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from examples.repl.application import TerminalApplication
from examples.repl.input import Draft
from examples.repl.protocol import Observation
from examples.repl.state import ScreenState, status_text
from tests.examples.repl.helpers import ApplicationClient, until


async def test_console_delivery_preserves_editor_input_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    cooked = False
    observed_mode_list: list[bool] = []

    class ObservedOutput(DummyOutput):
        def write(self, data: str) -> None:
            if "background delivery" in data:
                observed_mode_list.append(cooked)

    client = ApplicationClient()
    with create_pipe_input() as pipe:
        original_cooked_mode = pipe.cooked_mode

        @contextmanager
        def observed_cooked_mode():
            nonlocal cooked
            with original_cooked_mode():
                cooked = True
                try:
                    yield
                finally:
                    cooked = False

        monkeypatch.setattr(pipe, "cooked_mode", observed_cooked_mode)
        interface = TerminalApplication(client, input=pipe, output=ObservedOutput())
        task = asyncio.create_task(interface.run())
        try:
            await until(lambda: interface.application.is_running)
            interface.state.append("background delivery\n")
            await until(lambda: bool(observed_mode_list))
            assert observed_mode_list == [False]
            pipe.send_text("! ")
            await until(lambda: interface.state.draft.mode == "python")
            pipe.send_text("\x1b[200~print('typed')\x1b[201~\x1b\r")
            await until(lambda: len(client.submissions) == 1 and not interface.state.busy)
            assert client.submissions[0].text == "print('typed')"
            pipe.send_text("\x04")
            await asyncio.wait_for(task, 5)
        finally:
            if interface.application.is_running:
                interface.application.exit()
            await asyncio.gather(task, return_exceptions=True)
            client.session.close()


async def test_paste_preserves_python_indentation_without_submission() -> None:
    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput(), no_animation=True)
        task = asyncio.create_task(interface.run())
        await until(lambda: interface.application.is_running)
        pipe.send_text("! ")
        await until(lambda: interface.state.draft.mode == "python")
        text = "def sample():\n    return '日本語'\n\nsample()"
        pipe.send_text("\x1b[200~" + text + "\x1b[201~")
        await until(lambda: interface.editor.text == text)
        assert not client.submissions
        pipe.send_text("\x1b\r")
        await until(lambda: len(client.submissions) == 1 and not interface.state.busy)
        assert client.submissions[0].text == text
        assert client.submissions[0].mode == "python"
        assert "日本語" in interface.state.transcript
        pipe.send_text("\x04")
        await asyncio.wait_for(task, 5)


async def test_explicit_modes_preserve_comments_paste_and_python_errors() -> None:
    from nighthawk.parts.conversation import ConversationInput, ConversationResult, UserFacingBlock
    from tests.support.session import configured

    received: list[str] = []

    async def body(value: ConversationInput) -> ConversationResult:
        received.append(value.task)
        return ConversationResult((UserFacingBlock("answer", "received"),))

    client = ApplicationClient(configured(body).create_session())
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        task = asyncio.create_task(interface.run())
        try:
            await until(lambda: interface.application.is_running)
            assert interface._input_prompt() == "#> "
            pipe.send_text("\x1b[200~! pasted\x1b[201~")
            await until(lambda: interface.editor.text == "! pasted")
            assert interface.state.draft.mode == "natural_language" and not client.submissions
            pipe.send_text("\x1b\r")
            await until(lambda: len(client.submissions) == 1 and not interface.state.busy)
            assert received == ["! pasted"]
            pipe.send_text("! ")
            await until(lambda: interface.state.draft.mode == "python")
            assert interface.editor.text == "" and interface._input_prompt() == "!> "
            pipe.send_text("# comment\r42\x1b\r")
            await until(lambda: len(client.submissions) == 2 and not interface.state.busy)
            assert client.submissions[-1].text == "# comment\n42" and client.submissions[-1].mode == "python"
            assert interface.state.draft.mode == "python"
            pipe.send_text("if broken\x1b\r")
            await until(lambda: len(client.submissions) == 3 and not interface.state.busy)
            assert received == ["! pasted"] and "SyntaxError" in interface.state.transcript
            pipe.send_text("x\x7f")
            await until(lambda: interface.editor.text == "")
            # Ensure the queued deletion has run before checking its mode.
            await asyncio.sleep(0.05)
            assert interface.state.draft.mode == "python"
            pipe.send_text("\x7f")
            await until(lambda: interface.state.draft.mode == "natural_language")
            pipe.send_text("# heading\x1b\r")
            await until(lambda: len(client.submissions) == 4 and not interface.state.busy)
            assert received == ["! pasted", "# heading"]
            assert interface._input_prompt() == "#> "
            pipe.send_text("\x04")
            await asyncio.wait_for(task, 5)
        finally:
            if interface.application.is_running:
                interface.application.exit()
            await asyncio.gather(task, return_exceptions=True)
            client.session.close()


async def test_history_accept_does_not_submit_and_cancel_restores_cursor_and_mode() -> None:
    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        interface.state.input_history.add(Draft("samples = [1, 2, 3]", 8, "python"))
        task = asyncio.create_task(interface.run())
        await until(lambda: interface.application.is_running)
        original = Draft("  next 日本語\n  trailing  ", 4, "natural_language")
        interface._restore(original)
        pipe.send_text("\x12")
        await until(lambda: interface.state.view == "input_history")
        pipe.send_text("missing")
        await until(lambda: interface.search.text == "missing")
        pipe.send_text("\x03")
        await until(lambda: interface.state.view == "main")
        assert interface._draft() == original
        pipe.send_text("\x12")
        await until(lambda: interface.state.view == "input_history")
        pipe.send_text("samples\r")
        await until(lambda: interface.state.view == "main")
        assert interface._draft().text == "samples = [1, 2, 3]"
        assert interface._draft().mode == "python"
        assert not client.submissions
        pipe.send_text("\x03\x04")
        await asyncio.wait_for(task, 5)


async def test_next_draft_is_not_automatically_submitted() -> None:
    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        task = asyncio.create_task(interface.run())
        await until(lambda: interface.application.is_running)
        interface.state.receive(Observation(revision=100, observed_at=1, cell_id="unfinished", state="running", started_at=1))
        interface._restore(Draft("> next_value = 1", 16))
        pipe.send_text("\x1b\r")
        await until(lambda: "not queued" in interface.state.message)
        assert interface.editor.text == "> next_value = 1" and not client.submissions
        interface.state.receive(Observation(revision=101, observed_at=2, state="idle"))
        pipe.send_text("\x10")
        await until(lambda: interface.state.view == "palette")
        assert not client.submissions
        pipe.send_text("\x03")
        await until(lambda: interface.state.view == "main")
        pipe.send_text("\x03\x04")
        await asyncio.wait_for(task, 5)


def test_reduced_motion_keeps_elapsed_updates() -> None:
    state = ScreenState(observation=Observation(revision=1, observed_at=10, cell_id="cell", state="running", started_at=10))
    assert "1.0s" in status_text(state, 11, no_animation=True)
    assert "2.0s" in status_text(state, 12, no_animation=True)
    assert "Ctrl+C" not in status_text(state, 12, no_animation=True)


async def test_busy_palette_reads_apply_filters_without_contacting_owner() -> None:
    from nighthawk.session import LocalQuery, ReadLocalsRequest

    client = ApplicationClient()
    client.session.binding_name_to_value.update(samples=[1], unrelated=[2])
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        interface.state.receive(client.snapshot("busy"))

        async def forbidden(request):
            raise AssertionError("Retained query contacted the busy owner")

        client.invoke = forbidden
        await interface._operate(ReadLocalsRequest(LocalQuery(name="samples", maximum_character_count=2)))
        assert interface.state.view == "locals"
        assert [item.name for item in interface._local_rows()] == ["samples"]
        assert "Incomplete" in interface.state.message
        interface._activate()
        assert "preview: [1" in interface.detail.text and "unrelated" not in interface.detail.text
    client.session.close()


def test_cell_run_evidence_navigation_restores_search_and_record_anchor() -> None:
    from nighthawk.harness.evidence import EvidenceReference
    from nighthawk.parts.evidence import EvidenceItem, EvidenceProjection
    from nighthawk.session import HistoryRecord

    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        record = HistoryRecord(7, "cell", "answer", "日本語 answer", run_id="run")
        interface.state.record_index_to_record[7] = record
        interface.state.run_id_to_projection["run"] = EvidenceProjection(
            run_id="run",
            horizon_sequence=2,
            evidence_tuple=(
                EvidenceItem(reference=EvidenceReference(run_id="run", event_id="event"), sequence=2, excerpt="  exact\ntext", incomplete=True),
            ),
            next_starting_sequence=None,
            incomplete=True,
        )
        interface._open("history")
        interface.search.text = "日本語"
        interface._activate()
        assert interface.detail.text == record.text
        interface._open_runs()
        assert interface._run_rows() == ("run",)
        interface._target_run_id = "run"
        interface._open("evidence")
        interface._activate()
        assert interface.detail.text == "  exact\ntext"
        assert "incomplete" in interface.state.message
        interface._back()
        assert interface.state.view == "evidence"
        interface._back()
        assert interface.state.view == "runs"
        interface._back()
        assert interface.state.view == "detail" and interface.detail.text == record.text
        interface._back()
        assert interface.state.view == "history" and interface.search.text == "日本語"
        assert interface.inspector.selected_record_index == 7
    client.session.close()


def test_stream_inspection_keeps_channel_and_native_attribution_separate() -> None:
    from examples.repl.client import NativeOutput
    from examples.repl.protocol import OutputRecord

    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        interface.state.output_record_list.extend(
            (NativeOutput("stderr", "native text", "native/background"), OutputRecord(sequence=1, cell_id="cell", category="semantic", text="answer"))
        )
        interface._open("streams")
        interface.search.text = "stderr"
        assert len(interface._stream_rows()) == 1
        interface._activate()
        assert interface.detail.text == "native text"
        assert "not run evidence" in interface.state.message
    client.session.close()


async def test_background_list_opens_searchable_details_without_implicit_interruption() -> None:
    from nighthawk.session import BackgroundObservation, InterruptionResult, ListBackgroundRequest

    class Owner:
        interruptions = 0

        def read(self) -> BackgroundObservation:
            return BackgroundObservation("ignored", "owner", "retained work", "running", 10, True)

        def request_interrupt(self) -> InterruptionResult:
            self.interruptions += 1
            return InterruptionResult("requested", "ignored")

    client = ApplicationClient()
    owner = Owner()
    work_id = client.session.background.register(owner)
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        await interface._operate(ListBackgroundRequest())
        assert interface.state.view == "background"
        interface.search.text = "retained"
        interface._activate()
        assert interface.state.view == "detail" and work_id in interface.detail.text
        assert owner.interruptions == 0
        interface._back()
        assert interface.state.view == "background" and interface.search.text == "retained"
    owner.read = lambda: BackgroundObservation("ignored", "owner", "retained work", "stopped", 11, False)
    client.session.close()


async def test_plain_history_detail_recovers_retained_text_omitted_from_a_page() -> None:
    from dataclasses import replace

    client = ApplicationClient()
    original = "    preserved indentation\n" * 100
    record = client.session.history._append(cell_id="cell", kind="answer", text=original)
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        interface.state.record_index_to_record[record.record_index] = replace(record, text=original[:10], incomplete=True)
        interface._open("history")
        interface._activate()
        await until(lambda: interface.detail.text == original)
        assert interface.state.message == "Retained semantic text"
        assert not interface.state.record_index_to_record[record.record_index].incomplete
    client.session.close()


def test_status_uses_owner_state_and_ignores_other_cell_interruption() -> None:
    from dataclasses import replace

    from nighthawk.session import ExecutionObservation

    execution = ExecutionObservation("cell", (), "queued", "Running Python", 10, 11, 2, True)
    state = ScreenState(observation=Observation(revision=2, observed_at=11, cell_id="cell", state="queued", execution=execution))
    assert status_text(state, 12, no_animation=True).startswith("Queued")
    state.receive(Observation(revision=3, observed_at=12, cell_id="cell", state="running", execution=replace(execution, state="running")))
    state.pending_interruption_cell_id = "cell"
    assert status_text(state, 12, no_animation=True).startswith("Interrupt sent; awaiting owner")
    state.receive(
        Observation(
            revision=4, observed_at=13, cell_id="cell", state="interruption_requested", execution=replace(execution, state="interruption_requested")
        )
    )
    state.pending_interruption_cell_id = None
    assert status_text(state, 13, no_animation=True).startswith("Interruption requested; cessation unconfirmed")
    state.receive(
        Observation(revision=5, observed_at=14, cell_id="next", state="running", execution=replace(execution, cell_id="next", state="running"))
    )
    state.pending_interruption_cell_id = "cell"
    assert status_text(state, 14, no_animation=True).startswith("Running Python")


async def test_failed_diagnostics_close_only_after_worker_death_without_session_exit() -> None:
    from examples.repl.client import WorkerExited
    from examples.repl.protocol import Failure

    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        task = asyncio.create_task(interface.run())
        try:
            await until(lambda: interface.application.is_running)
            draft = Draft("next draft", 3, "python")
            interface._restore(draft)
            client.events.put_nowait(Failure(phase="transport", detail="connection lost"))
            await until(lambda: interface.state.failure)
            interface._action("close_diagnostics")
            assert not task.done() and not interface.state.diagnostics_closed
            assert "supervision continues" in interface.state.message
            client.events.put_nowait(WorkerExited(7))
            await until(lambda: interface.state.worker_exit_code == 7)
            interface._action("inspect")
            await until(lambda: interface.state.view == "history")
            assert interface._draft() == draft
            pipe.send_text("\x1b[21~")
            assert await asyncio.wait_for(task, 5) == 1
            assert not interface.state.closed and not client.session._disposed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            client.session.close()


async def test_failed_interface_task_unwinds_the_terminal_instead_of_leaving_it_open() -> None:
    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        task = asyncio.create_task(interface.run())
        try:
            await until(lambda: interface.application.is_running)

            async def failed_delivery() -> None:
                raise RuntimeError("expected presentation failure")

            interface._spawn(failed_delivery())
            with pytest.raises(RuntimeError, match="expected presentation failure"):
                await asyncio.wait_for(task, 5)
            assert not interface.application.is_running
            assert interface.state.failure and not interface.state.closed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            client.session.close()


async def test_vertical_history_recall_edits_copies_and_submits_a_new_cell() -> None:
    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        interface.state.input_history.add(Draft("first\nlast", mode="python"))
        interface.state.input_history.add(Draft("recent"))
        original_history = tuple(interface.state.input_history.entries)
        task = asyncio.create_task(interface.run())
        try:
            await until(lambda: interface.application.is_running)
            interface._restore(Draft("unsent", 0, "python"))
            pipe.send_text("\x1b[A")
            await until(lambda: interface.editor.text == "recent")
            assert interface._draft().mode == "natural_language"
            pipe.send_text("X\x1b[A")
            await until(lambda: interface.editor.text == "first\nlast")
            assert interface.editor.document.cursor_position_row == 1
            assert interface._draft().mode == "python"
            pipe.send_text("\x1b[A")
            await until(lambda: interface.editor.document.cursor_position_row == 0)
            pipe.send_text("\x1b[B\x1b[B")
            await until(lambda: interface.editor.text == "Xrecent")
            pipe.send_text("\x1b[B")
            await until(lambda: interface.editor.text == "unsent")
            assert interface._draft().mode == "python"
            assert tuple(interface.state.input_history.entries) == original_history
            assert not client.submissions
            # Search retrieves the immutable source, not the temporary Xrecent copy.
            pipe.send_text("\x12recent\r")
            await until(lambda: interface.state.view == "main" and interface.editor.text == "recent")
            interface._restore(Draft("10 + 20", 7, "python"))
            pipe.send_text("\x1b\r")
            await until(lambda: len(client.submissions) == 1 and not interface.state.busy)
            assert tuple(interface.state.input_history.entries[:2]) == original_history
            assert interface.state.input_history.entries[-1].text == "10 + 20"
            pipe.send_text("\x04")
            await asyncio.wait_for(task, 5)
        finally:
            if interface.application.is_running:
                interface.application.exit()
            await asyncio.gather(task, return_exceptions=True)
            client.session.close()


async def test_up_down_traverses_wrapped_rows_before_recalling_history() -> None:
    from prompt_toolkit.data_structures import Size

    class NarrowOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=30, columns=12)

    client = ApplicationClient()
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=NarrowOutput())
        interface.state.input_history.add(Draft("history"))
        task = asyncio.create_task(interface.run())
        try:
            await until(lambda: interface.application.is_running)
            text = "日本語abcdefghijk"
            interface._restore(Draft(text, len(text)))
            await until(lambda: interface.editor.window.render_info is not None)
            pipe.send_text("\x1b[A")
            await until(lambda: interface.editor.buffer.cursor_position < len(text))
            assert interface.editor.text == text
            pipe.send_text("\x1b[A")
            await until(lambda: interface.editor.text == "history")
            pipe.send_text("\x1b[B")
            await until(lambda: interface.editor.text == text)
            first_position = interface.editor.buffer.cursor_position
            pipe.send_text("\x1b[B")
            await until(lambda: interface.editor.buffer.cursor_position > first_position)
            assert interface.editor.text == text
            assert [entry.text for entry in interface.state.input_history.entries] == ["history"]
            pipe.send_text("\x03\x04")
            await asyncio.wait_for(task, 5)
        finally:
            if interface.application.is_running:
                interface.application.exit()
            await asyncio.gather(task, return_exceptions=True)
            client.session.close()
