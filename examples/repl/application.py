"""One persistent terminal application with explicit input and execution ownership."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, ScrollOffsets, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import Output
from prompt_toolkit.widgets import TextArea

from examples.repl.client import NativeOutput, WorkerClient, WorkerError, WorkerExited
from examples.repl.input import Draft, StaticCompleter, visual_cursor_rows
from examples.repl.inspectors import HistoryInspector, InspectorLocation
from examples.repl.operations import KEY_BINDING_TUPLE, ParameterField, operation_fields, terminal_help, typed_request
from examples.repl.protocol import Closed, Completed, Failure, Observation, OperationResponse, OutputRecord, Ready, Response
from examples.repl.rendering import CompactConsole, describe_result, interactive_text
from examples.repl.state import ScreenState, status_text
from nighthawk.parts.evidence import EvidenceProjection, EvidenceQuery
from nighthawk.session import (
    BackgroundObservation,
    ExitRequest,
    ExitResult,
    HelpRequest,
    HistoryPage,
    InspectLocalRequest,
    InterruptBackgroundRequest,
    InterruptExecutionRequest,
    ListBackgroundRequest,
    ListOperationsRequest,
    LocalBindingObservation,
    LocalPage,
    LocalQuery,
    OperationDescriptor,
    OperatorRequest,
    PlainTextObservation,
    ReadEvidenceRequest,
    ReadExecutionRequest,
    ReadHistoryRequest,
    ReadLocalsRequest,
    ReadTextRequest,
)
from nighthawk.session.formatting import describe_operation
from nighthawk.session.operator import filter_local_page


class TerminalApplication:
    def __init__(
        self,
        client: WorkerClient,
        *,
        input: Input | None = None,
        output: Output | None = None,
        no_animation: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.state = ScreenState()
        self.inspector = HistoryInspector()
        self.clock = clock
        self.no_animation = no_animation
        self.completer = StaticCompleter()
        self.console = CompactConsole()
        self.editor = TextArea(multiline=True, completer=self.completer, complete_while_typing=False, prompt=self._input_prompt)
        self.editor.window.height = lambda: min(5, self.editor.document.line_count)
        self.transcript = TextArea(read_only=True, scrollbar=True, wrap_lines=True, focusable=True)
        self.search = TextArea(multiline=False, prompt="Search: ", height=1)
        self.detail = TextArea(read_only=True, scrollbar=True, wrap_lines=False, focusable=True)
        self.parameters = TextArea(multiline=False, height=1)
        self._descriptor: OperationDescriptor | None = None
        self._field_tuple: tuple[ParameterField, ...] = ()
        self._value_list: list[str] = []
        self._field_position = 0
        self._target_run_id: str | None = None
        self._run_id_tuple: tuple[str, ...] = ()
        self._background_tuple: tuple[BackgroundObservation, ...] = ()
        self._selected_work_id: str | None = None
        self._last_local_query = LocalQuery()
        self._last_local_page: LocalPage | None = None
        self._editing_filters = False
        self._view_revision = 0
        self._detail_record_index: int | None = None
        self._task_set: set[asyncio.Task[None]] = set()
        self.search.buffer.on_text_changed += self._search_changed
        self._rows_control = FormattedTextControl(self._rows_text, focusable=False, show_cursor=False)
        modal = ConditionalContainer(
            HSplit(
                [
                    ConditionalContainer(
                        self.search,
                        Condition(
                            lambda: self.state.view in ("palette", "history", "input_history", "streams", "runs", "evidence", "locals", "background")
                        ),
                    ),
                    ConditionalContainer(
                        Window(self._rows_control, wrap_lines=True, scroll_offsets=ScrollOffsets(top=1, bottom=2)),
                        Condition(
                            lambda: self.state.view in ("palette", "history", "input_history", "streams", "runs", "evidence", "locals", "background")
                        ),
                    ),
                    ConditionalContainer(self.detail, Condition(lambda: self.state.view in ("detail", "parameters"))),
                    ConditionalContainer(
                        Window(FormattedTextControl(self._parameter_hint), height=1), Condition(lambda: self.state.view == "parameters")
                    ),
                    ConditionalContainer(
                        self.parameters, Condition(lambda: self.state.view == "parameters" and self._field_position < len(self._field_tuple))
                    ),
                ]
            ),
            Condition(lambda: self.state.view not in ("main", "output")),
        )
        root = HSplit(
            [
                ConditionalContainer(
                    Window(FormattedTextControl(lambda: interactive_text(self.state.console_partial_text)), height=1),
                    Condition(lambda: self.state.view == "main" and bool(self.state.console_partial_text)),
                ),
                ConditionalContainer(self.editor, Condition(lambda: self.state.view == "main")),
                ConditionalContainer(self.transcript, Condition(lambda: self.state.view == "output")),
                modal,
                Window(
                    FormattedTextControl(
                        lambda: interactive_text(
                            status_text(
                                self.state,
                                self.clock(),
                                no_animation=self.no_animation,
                                columns=self.application.output.get_size().columns,
                            )
                        )
                    ),
                    height=1,
                ),
            ]
        )
        self.application: Application[None] = Application(
            layout=Layout(root, focused_element=self.editor),
            key_bindings=self._bindings(),
            full_screen=False,
            mouse_support=False,
            input=input,
            output=output,
            refresh_interval=0.1,
        )

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._task_set.add(task)

        def finished(completed: asyncio.Task[None]) -> None:
            self._task_set.discard(completed)
            if not completed.cancelled() and (error := completed.exception()) is not None:
                # An unhandled UI task failure must not strand the terminal in
                # raw mode. run() still supervises the disconnected owner.
                self.state.failure = True
                if self.application.is_running and not self.application.is_done:
                    self.application.exit(exception=error)

        task.add_done_callback(finished)

    def _input_prompt(self) -> str:
        return "!> " if self.state.draft.mode == "python" else "#> "

    def _report(self, text: str) -> None:
        self.state.message = text
        self.state.append(text + "\n")
        self.application.invalidate()

    def _draft(self) -> Draft:
        return Draft(self.editor.text, self.editor.buffer.cursor_position, self.state.draft.mode)

    def _restore(self, draft: Draft) -> None:
        self.state.draft = draft
        self.editor.buffer.set_document(Document(draft.text, draft.cursor), bypass_readonly=True)

    def _move_editor(self, direction: int) -> None:
        information = self.editor.window.render_info
        width = information.window_width if information is not None else self.application.output.get_size().columns
        rows = visual_cursor_rows(self.editor.text, width, self._input_prompt())
        position = self.editor.buffer.cursor_position
        row_index, column = next((i, column) for i, row in enumerate(rows) for cursor, column in row if cursor == position)
        preferred = self.editor.buffer.preferred_column
        preferred = column if preferred is None else preferred
        following = row_index + direction
        if 0 <= following < len(rows):
            target = rows[following]
        else:
            draft = self.state.input_history.move(self._draft(), direction)
            if draft is None:
                return
            self._restore(draft)
            rows = visual_cursor_rows(draft.text, width, self._input_prompt())
            target = rows[-1] if direction < 0 else rows[0]
        self.editor.buffer.cursor_position = min(target, key=lambda entry: abs(entry[1] - preferred))[0]
        self.editor.buffer.preferred_column = preferred

    def _bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        editor_navigation = Condition(
            lambda: (
                self.state.view == "main"
                and self.application.layout.current_control is self.editor.control
                and self.editor.buffer.complete_state is None
                and self.editor.buffer.selection_state is None
            )
        )

        @bindings.add("up", filter=editor_navigation)
        def editor_previous(event: KeyPressEvent) -> None:
            for _ in range(event.arg):
                self._move_editor(-1)

        @bindings.add("down", filter=editor_navigation)
        def editor_following(event: KeyPressEvent) -> None:
            for _ in range(event.arg):
                self._move_editor(1)

        @bindings.add(
            " ",
            filter=Condition(
                lambda: (
                    self.state.view == "main"
                    and self.state.draft.mode == "natural_language"
                    and self.editor.text == "!"
                    and self.editor.buffer.cursor_position == 1
                )
            ),
        )
        def python_mode(event: KeyPressEvent) -> None:
            self._restore(Draft(mode="python"))

        @bindings.add("backspace", filter=Condition(lambda: self.state.view == "main" and not self.editor.text))
        def natural_mode(event: KeyPressEvent) -> None:
            self._restore(Draft())

        for binding in KEY_BINDING_TUPLE:

            def handle(event: KeyPressEvent, action: str = binding.action) -> None:
                self._action(action)

            bindings.add(*binding.keys)(handle)

        @bindings.add("enter", filter=Condition(lambda: self.state.view != "main"))
        def activate(event: KeyPressEvent) -> None:
            self._activate()

        @bindings.add("escape", eager=False)
        def back(event: KeyPressEvent) -> None:
            if self.state.view != "main":
                self._back()

        @bindings.add(
            "up",
            filter=Condition(
                lambda: self.state.view in ("palette", "history", "input_history", "streams", "runs", "evidence", "locals", "background")
            ),
        )
        def previous(event: KeyPressEvent) -> None:
            self._move(-1)

        @bindings.add(
            "down",
            filter=Condition(
                lambda: self.state.view in ("palette", "history", "input_history", "streams", "runs", "evidence", "locals", "background")
            ),
        )
        def following(event: KeyPressEvent) -> None:
            self._move(1)

        @bindings.add("tab", filter=Condition(lambda: self.state.view == "parameters"))
        def next_field(event: KeyPressEvent) -> None:
            self._activate()

        return bindings

    def _action(self, action: str) -> None:
        if action == "close_diagnostics":
            if self.state.failure and self.state.worker_exit_code is not None:
                self.state.diagnostics_closed = True
                if self.application.is_running:
                    self.application.exit()
            elif self.state.failure:
                self._report("Owner cessation is unconfirmed; supervision continues until the worker exits")
        elif action == "interrupt":
            if self.state.view != "main":
                self._back()
            elif self.state.busy:
                self._spawn(self._interrupt())
            else:
                self.state.input_history.reset_navigation()
                self._restore(Draft(mode=self.state.draft.mode))
        elif action == "exit":
            if self.state.view == "main" and not self.editor.text:
                self._spawn(self._operate(ExitRequest()))
        elif action in ("submit", "literal"):
            if self.state.view == "main":
                self._spawn(self._submit(literal=action == "literal"))
        elif action == "history":
            if self.state.view == "main":
                self.state.input_history.open(self._draft())
                self._open("input_history")
        elif action == "palette":
            self._open("palette")
        elif action == "inspect":
            self._open("history")
            self._spawn(self._load_history())
        elif action == "help":
            self._show_detail(
                "Nighthawk terminal\n\n"
                + terminal_help()
                + f"\n\nDirectory: {self.client.directory}\nExecution model: {self.client.execution_model or 'default'}\n"
                + f"Decision model: {self.client.decision_model or 'default'}\n"
                + f"Input mode: {self._draft().submission()[1]}\n\nSession operations\n"
                + self._operation_help()
            )
        elif action == "streams":
            self._open("streams")
        elif action == "runs":
            self._open_runs()
        elif action == "filters":
            self._open("palette")
            self.search.text = "session.history.read"
            self._activate()
            self._editing_filters = True
            self._field_tuple = tuple(field for field in self._field_tuple if field.path[0] == "filters")
            self._value_list = [getattr(self.inspector.filters, field.path[1]) or "" for field in self._field_tuple]
            self._field_position = 0
            self.parameters.text = self._value_list[0]
            self.detail.text = "History filters: kind, cell Id, run Id, confirmed cell outcome. Empty fields match all."
        elif action == "background_interrupt":
            rows = self._background_rows()
            work_id = (
                rows[min(self.state.selected_position, len(rows) - 1)].work_id
                if self.state.view == "background" and rows
                else self._selected_work_id
                if self.state.view == "detail"
                else None
            )
            if work_id is not None:
                self._spawn(self._operate(InterruptBackgroundRequest(work_id)))
        elif action == "next_page":
            if self._target_run_id is not None and self.state.view == "evidence":
                projection = self.state.run_id_to_projection.get(self._target_run_id)
                if projection is not None and projection.next_starting_sequence is not None:
                    self._spawn(self._load_evidence(self._target_run_id, projection.next_starting_sequence))
            elif self._last_local_page is not None and self._last_local_page.next_starting_index is not None:
                self._spawn(
                    self._operate(ReadLocalsRequest(replace(self._last_local_query, starting_index=self._last_local_page.next_starting_index)))
                )
        elif action in ("scroll", "scroll_down"):
            if self.state.view in ("main", "output"):
                if self.state.view == "main":
                    self._open("output")
                self.state.follow_latest = False
                self.application.layout.focus(self.transcript)
                if action == "scroll":
                    self.transcript.buffer.cursor_up(count=8)
                else:
                    self.transcript.buffer.cursor_down(count=8)
        elif action == "latest":
            self.state.follow_latest = True
            if self.state.view == "output":
                self._back()
            if self.state.view == "main":
                self.application.layout.focus(self.editor)
            self.transcript.buffer.cursor_position = len(self.transcript.text)
            if self.state.view == "history":
                rows = self._history_rows()
                self.inspector.select(rows, len(rows) - 1)
                self.state.selected_position = max(0, len(rows) - 1)
        self.application.invalidate()

    def _open(self, view: str) -> None:
        self._view_revision += 1
        if self.state.view not in ("main", "parameters") and self.state.view != view:
            self.inspector.return_location_list.append(self._location())
        if self.state.view == "main":
            self.state.draft = self._draft()
        self.state.view = view  # type: ignore[assignment]
        self._descriptor = None
        self.state.selected_position = 0
        self.search.text = ""
        self.application.layout.focus(self.transcript if view == "output" else self.search)
        self.state.message = "Retained observations; new requests remain pending until the owner can evaluate them"

    def _back(self) -> None:
        self._view_revision += 1
        self._editing_filters = False
        if self.state.view == "input_history":
            self._restore(self.state.input_history.cancel())
        if self.inspector.return_location_list:
            location = self.inspector.return_location_list.pop()
            self.state.view = location.view  # type: ignore[assignment]
            self.search.text = location.query
            self.state.selected_position = location.position
            self.inspector.selected_record_index = location.record_index
            self._detail_record_index = location.record_index
            self._target_run_id = location.run_id
            self.detail.text = location.detail_text
            self.application.layout.focus(self.detail if location.view == "detail" else self.transcript if location.view == "output" else self.search)
        else:
            self.state.view = "main"
            self.application.layout.focus(self.editor)

    def _location(self) -> InspectorLocation:
        return InspectorLocation(
            self.state.view,
            self.search.text,
            self.inspector.selected_record_index,
            self.state.selected_position,
            self.detail.text,
            self._target_run_id,
        )

    def _show_detail(self, text: str) -> None:
        self._view_revision += 1
        if self.state.view != "background":
            self._selected_work_id = None
        if self.state.view not in ("main", "parameters", "detail"):
            self.inspector.return_location_list.append(self._location())
        elif self.state.view == "main":
            self.state.draft = self._draft()
        self.state.view = "detail"
        self.detail.text = interactive_text(text)
        self.detail.buffer.cursor_position = 0
        self.application.layout.focus(self.detail)
        self.application.invalidate()

    def _operation_help(self) -> str:
        observed = self.state.observation
        return (
            "\n\n".join(describe_operation(item) for item in observed.operation_tuple) if observed is not None else "Operation metadata unavailable"
        )

    def _descriptors(self) -> tuple[OperationDescriptor, ...]:
        observed = self.state.observation
        if observed is None:
            return ()
        return tuple(item for item in observed.operation_tuple if self.search.text.casefold() in (item.name + " " + item.description).casefold())

    def _history_rows(self):
        self.inspector.query = self.search.text
        return self.inspector.rows(
            tuple(self.state.record_index_to_record[i] for i in sorted(self.state.record_index_to_record)), self.state.cell_id_to_outcome
        )

    def _local_rows(self) -> tuple[LocalBindingObservation, ...]:
        if self._last_local_page is None:
            return ()
        query = self.search.text.casefold()
        return tuple(
            item
            for item in self._last_local_page.binding_tuple
            if query in (item.name + " " + item.type_description + " " + item.exposure).casefold()
        )

    def _background_rows(self) -> tuple[BackgroundObservation, ...]:
        query = self.search.text.casefold()
        return tuple(
            item
            for item in self._background_tuple
            if query in (item.work_id + " " + item.owner + " " + item.state + " " + item.description).casefold()
        )

    def _stream_rows(self):
        query = self.search.text.casefold()
        return tuple(
            (i, record)
            for i, record in enumerate(self.state.output_record_list)
            if (not isinstance(record, OutputRecord) or record.category != "semantic")
            and query in (record.text + " " + getattr(record, "category", "gap") + " " + (getattr(record, "cell_id", None) or "")).casefold()
        )

    def _run_rows(self) -> tuple[str, ...]:
        return tuple(identity for identity in self._run_id_tuple if self.search.text in identity)

    def _evidence_rows(self):
        projection = self.state.run_id_to_projection.get(self._target_run_id or "")
        return (
            tuple(item for item in projection.evidence_tuple if self.search.text.casefold() in (item.excerpt + item.reference.event_id).casefold())
            if projection is not None
            else ()
        )

    def _open_runs(self) -> None:
        record_index = self._detail_record_index if self.state.view == "detail" else self.inspector.selected_record_index
        record = self.state.record_index_to_record.get(record_index) if record_index is not None else None
        if record is None and self.state.view == "history":
            record = self.inspector.select(self._history_rows(), self.state.selected_position)
        cell_id = record.cell_id if record is not None else None
        related_run_id: str | None = None
        if self.state.view == "background" and self._background_rows():
            work = self._background_rows()[min(self.state.selected_position, len(self._background_rows()) - 1)]
            cell_id, related_run_id = work.cell_id, work.run_id
        elif self.state.view == "detail" and self._selected_work_id is not None:
            work = next(item for item in self._background_tuple if item.work_id == self._selected_work_id)
            cell_id, related_run_id = work.cell_id, work.run_id
        elif self.state.view == "streams" and self._stream_rows():
            stream = self._stream_rows()[min(self.state.selected_position, len(self._stream_rows()) - 1)][1]
            cell_id = stream.cell_id if isinstance(stream, (OutputRecord, NativeOutput)) else None
        identities = {
            item.run_id
            for item in self.state.record_index_to_record.values()
            if item.run_id is not None and (cell_id is None or item.cell_id == cell_id)
        }
        if related_run_id is not None:
            identities.add(related_run_id)
        for output in self.state.output_record_list:
            if isinstance(output, OutputRecord) and (cell_id is None or output.cell_id == cell_id):
                if output.run_id is not None:
                    identities.add(output.run_id)
                identities.update(reference.run_id for reference in output.evidence_reference_tuple)
        observed = self.state.observation
        if observed is not None and (cell_id is None or observed.cell_id == cell_id):
            identities.update(observed.run_id_tuple)
        self._run_id_tuple = tuple(sorted(identities))
        self._open("runs")
        self.state.message = f"Runs for cell {cell_id or 'all retained cells'}; missing identities are unavailable"

    async def _load_evidence(self, run_id: str, starting_sequence: int = 0) -> None:
        self.state.message = "Retained evidence remains readable; fresh evidence awaiting owner evaluation"
        try:
            result = await self.client.invoke(ReadEvidenceRequest(run_id, EvidenceQuery(starting_sequence=starting_sequence)))
            if isinstance(result, EvidenceProjection):
                self.state.run_id_to_projection[run_id] = result
                self.state.selected_position = 0
                self.state.message = f"Evidence run={run_id}; horizon={result.horizon_sequence}" + (
                    " | F5 next page" if result.next_starting_sequence is not None else ""
                )
        except WorkerError as error:
            self._report(f"Evidence unavailable: {error}")
        self.application.invalidate()

    async def _load_text(self, record_index: int, view_revision: int) -> None:
        try:
            result = await self.client.invoke(ReadTextRequest(record_index))
            if isinstance(result, PlainTextObservation):
                previous = self.state.record_index_to_record.get(record_index)
                if previous is not None and result.available:
                    self.state.record_index_to_record[record_index] = replace(previous, text=result.text, incomplete=result.incomplete)
                if self.state.view == "detail" and self._view_revision == view_revision:
                    self.detail.text = interactive_text(result.text)
                    self.state.message = result.description
        except WorkerError as error:
            if self._view_revision == view_revision:
                self._report(f"Retained excerpt; full text unavailable: {error}")
        self.application.invalidate()

    def _rows_text(self) -> FormattedText:
        if self.state.view == "palette":
            rows = self._descriptors()
            texts = [
                f"{item.name}{item.signature}\n  {item.description}" + (f" [Unavailable: {item.unavailable_reason}]" if not item.available else "")
                for item in rows
            ]
        elif self.state.view == "history":
            history = self._history_rows()
            texts = [
                f"{item.record_index}: {item.kind} cell={item.cell_id}"
                + (f" (cell {self.console.cell_id_to_number[item.cell_id]})" if item.cell_id in self.console.cell_id_to_number else "")
                + (f" run={item.run_id}" if item.run_id is not None else "")
                + f"\n  {item.text[:300]}"
                + (" [incomplete]" if item.incomplete else "")
                for item in history
            ]
        elif self.state.view == "locals":
            texts = [f"{item.name}: {item.type_description} = {item.preview}\n  {item.exposure_description}" for item in self._local_rows()]
        elif self.state.view == "background":
            texts = [f"{item.work_id}: {item.state} owner={item.owner}\n  {item.description}" for item in self._background_rows()]
        elif self.state.view == "streams":
            texts = [
                f"{i}: {getattr(record, 'category', 'gap')} cell={record.cell_id if isinstance(record, (OutputRecord, NativeOutput)) else '-'} {getattr(record, 'phase', '')}\n  {record.text[:300]}"
                for i, record in self._stream_rows()
            ]
        elif self.state.view == "runs":
            texts = [
                (f"run {self.console.run_id_to_number[identity]}: " if identity in self.console.run_id_to_number else "") + identity
                for identity in self._run_rows()
            ]
        elif self.state.view == "evidence":
            texts = [f"{item.sequence}: event={item.reference.event_id}\n  {item.excerpt[:200]}" for item in self._evidence_rows()]
        else:
            texts = [entry.text for entry in self.state.input_history.search(self.search.text)]
        if not texts:
            return FormattedText([("", "No matches; Escape restores your draft")])
        position = (
            self.inspector.position(self._history_rows())
            if self.state.view == "history" and self.inspector.selected_record_index is not None
            else min(self.state.selected_position, len(texts) - 1)
        )
        start = max(0, position - 3)
        fragments = FormattedText()
        if self.state.view == "history":
            fragments.append(("", self.state.history_scope() + "\n"))
        elif self.state.view == "background" and self.state.message.startswith("Exit refused"):
            fragments.append(("", "Exit refused: managed background work has not ceased\n"))
        elif self.state.view == "locals" and self._last_local_page is not None:
            fragments.append(("", interactive_text(self.state.message) + "\n"))
        for i in range(start, min(start + 10, len(texts))):
            if i == position:
                # Let the layout scroll to the selection using actual wrapped
                # terminal rows, even while the search field keeps input focus.
                fragments.append(("[SetCursorPosition]", ""))
            fragments.append(("", ("> " if i == position else "  ") + interactive_text(texts[i]) + "\n"))
        return fragments

    def _search_changed(self, buffer) -> None:
        self.state.selected_position = 0
        self.inspector.selected_record_index = None

    def _move(self, amount: int) -> None:
        if self.state.view == "history":
            rows = self._history_rows()
            self.state.selected_position = min(
                max(
                    0, (self.inspector.position(rows) if self.inspector.selected_record_index is not None else self.state.selected_position) + amount
                ),
                max(0, len(rows) - 1),
            )
            self.inspector.select(rows, self.state.selected_position)
        else:
            self.state.selected_position = max(0, self.state.selected_position + amount)
        self.application.invalidate()

    def _activate(self) -> None:
        position = self.state.selected_position
        if self.state.view == "input_history":
            self._restore(self.state.input_history.accept(self.search.text, position))
            self.state.view = "main"
            self.application.layout.focus(self.editor)
        elif self.state.view == "history":
            rows = self._history_rows()
            selected = self.inspector.select(rows, self.inspector.position(rows) if self.inspector.selected_record_index is not None else position)
            if selected is not None:
                self._detail_record_index = selected.record_index
                self._show_detail(selected.text)
                self.state.message = (
                    "Retained excerpt; full text query awaiting owner" if selected.incomplete else "Plain semantic text; source is not executed"
                )
                if selected.incomplete:
                    self._spawn(self._load_text(selected.record_index, self._view_revision))
        elif self.state.view == "locals":
            rows = self._local_rows()
            if rows:
                self._selected_work_id = None
                self._show_detail(describe_result(rows[min(position, len(rows) - 1)]))
        elif self.state.view == "background":
            rows = self._background_rows()
            if rows:
                selected_work = rows[min(position, len(rows) - 1)]
                self._selected_work_id = selected_work.work_id
                self._show_detail(describe_result(selected_work))
                self.state.message = f"F6 requests interruption of {selected_work.work_id}; request acceptance is not cessation"
        elif self.state.view == "streams":
            rows = self._stream_rows()
            if rows:
                record = rows[min(position, len(rows) - 1)][1]
                self._show_detail(record.text)
                self.state.message = "Captured stream text; attribution can be unavailable; this is not run evidence"
        elif self.state.view == "runs":
            rows = self._run_rows()
            if rows:
                self._target_run_id = rows[min(position, len(rows) - 1)]
                self._open("evidence")
                self._spawn(self._load_evidence(self._target_run_id))
        elif self.state.view == "evidence":
            rows = self._evidence_rows()
            if rows:
                selected = rows[min(position, len(rows) - 1)]
                self._show_detail(selected.excerpt)
                self.state.message = f"Retained evidence event={selected.reference.event_id}; " + (
                    "incomplete projection; original source unavailable in this view" if selected.incomplete else "plain semantic excerpt"
                )
        elif self.state.view == "palette":
            descriptors = self._descriptors()
            if descriptors:
                self._descriptor = descriptors[min(position, len(descriptors) - 1)]
                self._field_tuple = operation_fields(self._descriptor)
                self._value_list = [field.default for field in self._field_tuple]
                self._field_position = 0
                self._show_detail(self._descriptor.name + self._descriptor.signature + "\n\n" + self._descriptor.description)
                self.state.view = "parameters"
                if self._field_tuple:
                    self.parameters.text = self._value_list[0]
                    self.application.layout.focus(self.parameters)
        elif self.state.view == "parameters":
            if self._field_position < len(self._field_tuple):
                self._value_list[self._field_position] = self.parameters.text
                self._field_position += 1
                if self._field_position < len(self._field_tuple):
                    self.parameters.text = self._value_list[self._field_position]
                    return
                self.application.layout.focus(self.detail)
            else:
                assert self._descriptor is not None
                try:
                    request = typed_request(self._descriptor.name, self._field_tuple, tuple(self._value_list))
                except (ValueError, TypeError) as error:
                    self._report(f"Invalid parameter: {error}")
                    self._field_position = 0
                    if self._field_tuple:
                        self.application.layout.focus(self.parameters)
                    return
                if self._editing_filters and isinstance(request, ReadHistoryRequest):
                    self._editing_filters = False
                    self.inspector.filters = request.filters
                    self.state.view = "history"
                    self.search.text = ""
                    self.application.layout.focus(self.search)
                    self._spawn(self._load_history())
                else:
                    self._spawn(self._operate(request))
        self.application.invalidate()

    def _parameter_hint(self) -> str:
        if self._field_position < len(self._field_tuple):
            return self._field_tuple[self._field_position].label + ": Enter accepts this field"
        return "Enter activates the displayed operation | Escape returns"

    async def _submit(self, *, literal: bool) -> None:
        if self.state.busy:
            self._report("The preceding cell is unfinished; the draft was preserved and was not queued")
            return
        draft = self._draft()
        if literal:
            draft = replace(draft, mode="natural_language")
        text, mode = draft.submission()
        self.state.submitting = True
        self.state.message = "Submission awaiting owner evaluation"
        self.state.append(("!> " if mode == "python" else "#> ") + text + "\n")
        try:
            response = await self.client.submit(text, mode=mode)
            if response.status != "submitted":
                self._report(response.detail)
                return
            self.state.input_history.add(draft)
            if self.editor.text == draft.text:
                self._restore(Draft(mode=self.state.draft.mode))
            if response.observation is not None:
                self.state.receive(response.observation)
            self.state.message = "Executing; edit the next draft freely"
        except WorkerError as error:
            self._report(str(error))
        finally:
            self.state.submitting = False
            self.application.invalidate()

    async def _interrupt(self) -> None:
        observed = self.state.observation
        if observed is None or observed.cell_id is None:
            return
        target = observed.cell_id
        self.state.pending_interruption_cell_id = target
        self.state.message = "Interruption request sent; awaiting owner evaluation"
        try:
            response = await self.client.interrupt(target)
            if response.observation is not None:
                self.state.receive(response.observation)
        except WorkerError as error:
            self._report(str(error))
        finally:
            if self.state.pending_interruption_cell_id == target:
                self.state.pending_interruption_cell_id = None
            self.application.invalidate()

    async def _operate(self, request: OperatorRequest) -> None:
        operation = request.operation
        view_revision = self._view_revision
        self.state.pending_operation = operation
        interruption_target = self.state.observation.cell_id if isinstance(request, InterruptExecutionRequest) and self.state.observation else None
        if interruption_target is not None:
            self.state.pending_interruption_cell_id = interruption_target
        self.state.message = f"{operation}: awaiting owner evaluation"
        try:
            # Already received observations stay available during synchronous work.
            observed = self.state.observation
            retained = self.state.busy or self.state.failure
            if retained and isinstance(request, ReadLocalsRequest) and observed is not None and observed.locals is not None:
                result = filter_local_page(observed.locals, request.query)
                self.state.message = f"Retained locals observed at {result.observed_at:.3f}; newer values unavailable"
            elif retained and isinstance(request, ReadExecutionRequest) and observed is not None and observed.execution is not None:
                result = observed.execution
                self.state.message = f"Execution last observed at {result.observed_at:.3f}"
            elif retained and isinstance(request, ListBackgroundRequest) and observed is not None:
                result = observed.background_tuple
                self.state.message = f"Background last observed at {observed.observed_at:.3f}"
            elif retained and isinstance(request, InspectLocalRequest) and observed is not None and observed.locals is not None:
                result = next(
                    (item for item in observed.locals.binding_tuple if item.name == request.name),
                    LocalBindingObservation(
                        request.name,
                        "unknown",
                        "<not observed>",
                        "unavailable",
                        "No retained observation",
                        observed.locals.observed_at,
                        observed.locals.revision,
                        True,
                        False,
                    ),
                )
            elif isinstance(request, ListOperationsRequest) and observed is not None:
                result = tuple(
                    item for item in observed.operation_tuple if request.query.casefold() in (item.name + " " + item.description).casefold()
                )
            elif isinstance(request, HelpRequest) and observed is not None:
                result = tuple(item for item in observed.operation_tuple if request.operation_name is None or item.name == request.operation_name)
            else:
                result = await self.client.invoke(request)
                self.state.message = f"{operation}: evaluated by owner"
            if view_revision != self._view_revision:
                self._report(f"{operation}: {describe_result(result)}")
                return
            if isinstance(result, ExitResult):
                self._report(f"Exit {result.status}. " + result.reason)
                if result.blocker_tuple:
                    self._background_tuple = result.blocker_tuple
                    self._open("background")
                    self.state.message = "Exit refused: managed background work has not ceased. Enter details; F6 requests selected interruption"
            elif isinstance(result, LocalPage):
                self._last_local_page = result
                if isinstance(request, ReadLocalsRequest):
                    self._last_local_query = replace(request.query, revision=result.revision)
                self._open("locals")
                self.state.message = f"Locals observed at {result.observed_at:.3f}, revision {result.revision}; search this page" + (
                    " | F5 more" if result.next_starting_index is not None else ""
                )
                if result.incomplete:
                    self.state.message += " | Incomplete observation"
                if result.unavailable_reason:
                    self.state.message += " | " + result.unavailable_reason
                if result.session_name_shadowed:
                    self.state.message += " | Python name session is shadowed; palette still addresses the owner"
            elif isinstance(request, ListBackgroundRequest):
                self._background_tuple = result  # type: ignore[assignment]
                self._open("background")
                self.state.message = "Retained background observations; Enter details; F6 requests selected interruption"
            else:
                self._show_detail(describe_result(result))
                if isinstance(result, PlainTextObservation):
                    self.state.message = result.description
        except (WorkerError, ValueError) as error:
            self._report(f"Operation failed: {error}")
        finally:
            if interruption_target is not None and self.state.pending_interruption_cell_id == interruption_target:
                self.state.pending_interruption_cell_id = None
            self.state.pending_operation = None
            self.application.invalidate()

    async def _load_history(self) -> None:
        self.state.message = "Retained history is searchable; additional history awaiting owner"
        position = 0
        horizon = None
        try:
            while True:
                page = await self.client.invoke(ReadHistoryRequest(starting_index=position, horizon=horizon))
                if not isinstance(page, HistoryPage):
                    break
                self.state.receive_history(page)
                horizon = page.horizon
                self.application.invalidate()
                if page.next_starting_index is None:
                    break
                position = page.next_starting_index
            self.state.message = "History observed; output and evidence remain distinct"
        except WorkerError as error:
            self._report(str(error))

    async def _emit_console(self, *, final: bool = False) -> None:
        text = self.state.take_console_text(final=final)
        if text:
            # This writer runs synchronously on the UI loop and never transfers
            # terminal ownership. A cooked-mode detour can translate an incoming
            # submission's carriage return into a newline during output floods.
            self.application.renderer.erase()
            self.application.output.enable_bracketed_paste()
            self.application.print_text(FormattedText([("", interactive_text(text))]))
            self.application.renderer.request_absolute_cursor_position()
            self.application.invalidate()

    def _output(self, record: OutputRecord) -> None:
        if record.sequence in self.state.delivered_sequence_set:
            return
        self.state.delivered_sequence_set.add(record.sequence)
        self.state.output_record_list.append(record)
        if record.category == "semantic":
            self.state.append(self.console.render(record))
        else:
            self.state.append(record.text)

    async def _receive(self) -> None:
        while not self.state.closed and not self.state.diagnostics_closed:
            try:
                event = await asyncio.wait_for(self.client.events.get(), 0.05)
            except TimeoutError:
                event = None
            if isinstance(event, (Ready, Completed)):
                self.state.receive(event.observation)
            elif isinstance(event, Observation):
                self.state.receive(event)
            elif isinstance(event, (Response, OperationResponse)) and event.observation is not None:
                self.state.receive(event.observation)
            if isinstance(event, Ready):
                self.state.message = "Ready; ! then Space in an empty Natural draft switches to Python"
            if isinstance(event, Completed):
                if event.output_incomplete:
                    self.state.append("[Completion presentation incomplete; inspect retained Session history]\n")
                for record in event.output_tuple:
                    self._output(record)
                if self.state.pending_interruption_cell_id == event.cell_id:
                    self.state.pending_interruption_cell_id = None
                self.state.message = f"Cell {event.status}; draft remains editable"
            if isinstance(event, Closed) or isinstance(event, Response) and event.status == "closed":
                self.state.closed = True
            if isinstance(event, Failure):
                self._report(event.detail)
                if event.phase != "execution":
                    self.state.failure = True
            if isinstance(event, WorkerExited):
                self.state.worker_exit_code = event.exit_code
                if not self.client.closed:
                    self.state.failure = True
                    self._report(f"Worker exited ({event.exit_code}); Session closure unconfirmed. Retained diagnostics remain available.")
            for record in self.client.read_presentation():
                if isinstance(record, OutputRecord):
                    self._output(record)
                else:
                    self.state.output_record_list.append(record)
                    self.state.append(record.text)
            text = interactive_text(self.state.transcript)
            if text != self.transcript.text:
                position = self.transcript.buffer.cursor_position
                self.transcript.buffer.set_document(
                    Document(text, len(text) if self.state.follow_latest else min(position, len(text))), bypass_readonly=True
                )
            observed = self.state.observation
            if observed is not None:
                self.completer.names = tuple(sorted(set(observed.binding_name_tuple) | {item.name for item in observed.operation_tuple}))
            await self._emit_console(final=self.state.closed)
            self.application.invalidate()
        await self.client.join()
        await self.client._join_readers()
        for record in self.client.read_presentation():
            if isinstance(record, OutputRecord):
                self._output(record)
            else:
                self.state.output_record_list.append(record)
                self.state.append(record.text)
        await self._emit_console(final=True)
        self.client.disconnect()
        if self.application.is_running:
            self.application.exit()

    async def run(self) -> int:
        try:
            await self.client.start()
            # Console delivery must inherit this application's terminal context.
            await self.application.run_async(pre_run=lambda: self._spawn(self._receive()))
        finally:
            for task in self._task_set:
                task.cancel()
            await asyncio.gather(*self._task_set, return_exceptions=True)
            self.client.disconnect()
            await self.client.join()
        return 1 if self.state.failure else 0
