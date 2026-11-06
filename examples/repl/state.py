"""Received evidence and presentation state; rendering does not query the execution owner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from prompt_toolkit.utils import get_cwidth

from nighthawk.agent.history import HistoryProjection
from nighthawk.agent.session import ConversationPage, ConversationRecord

from .client import NativeOutput, OutputGap
from .input import Draft, InputHistory
from .protocol import Observation, OutputRecord


@dataclass
class ScreenState:
    view: Literal[
        "main", "output", "palette", "history", "input_history", "detail", "parameters", "streams", "runs", "evidence", "locals", "background"
    ] = "main"
    draft: Draft = field(default_factory=Draft)
    input_history: InputHistory = field(default_factory=InputHistory)
    observation: Observation | None = None
    record_index_to_record: dict[int, ConversationRecord] = field(default_factory=dict)
    cell_id_to_outcome: dict[str, str] = field(default_factory=dict)
    history_horizon: int = 0
    output_record_list: list[OutputRecord | NativeOutput | OutputGap] = field(default_factory=list)
    run_id_to_projection: dict[str, HistoryProjection] = field(default_factory=dict)
    delivered_sequence_set: set[int] = field(default_factory=set)
    transcript: str = ""
    pending_console_text: str = ""
    message: str = "Starting execution owner"
    submitting: bool = False
    pending_interruption_cell_id: str | None = None
    pending_operation: str | None = None
    selected_position: int = 0
    selected_record_index: int | None = None
    follow_latest: bool = True
    closed: bool = False
    failure: bool = False
    worker_exit_code: int | None = None
    diagnostics_closed: bool = False

    @property
    def busy(self) -> bool:
        return self.submitting or self.observation is not None and self.observation.cell_id is not None

    def receive(self, observation: Observation) -> None:
        if self.observation is not None and observation.revision < self.observation.revision:
            return
        self.observation = observation
        if self.pending_interruption_cell_id != observation.cell_id:
            self.pending_interruption_cell_id = None
        if observation.history is not None:
            self.receive_history(observation.history)

    def receive_history(self, page: ConversationPage) -> None:
        self.history_horizon = max(self.history_horizon, page.horizon)
        self.cell_id_to_outcome.update(page.cell_outcome_tuple)
        for record in page.record_tuple:
            existing = self.record_index_to_record.get(record.record_index)
            if existing is None or len(record.text) >= len(existing.text):
                self.record_index_to_record[record.record_index] = record

    def history_scope(self) -> str:
        received = len(self.record_index_to_record)
        incomplete = any(record.incomplete for record in self.record_index_to_record.values())
        return f"History: received {received}/{self.history_horizon} records; case-sensitive" + ("; incomplete excerpts" if incomplete else "")

    def append(self, text: str) -> None:
        self.transcript += text
        if len(self.transcript) > 200000:
            self.transcript = "[Earlier presentation output unavailable]\n" + self.transcript[-150000:]
        self.pending_console_text += text
        if len(self.pending_console_text) > 32000:
            self.pending_console_text = "[Console delivery incomplete; inspect retained output]\n" + self.pending_console_text[-24000:]

    def take_console_text(self, *, final: bool = False) -> str:
        boundary = len(self.pending_console_text) if final else self.pending_console_text.rfind("\n") + 1
        text, self.pending_console_text = self.pending_console_text[:boundary], self.pending_console_text[boundary:]
        return text + "\n" if text and not text.endswith("\n") else text

    @property
    def console_partial_text(self) -> str:
        return self.pending_console_text.rsplit("\n", 1)[-1]


def _fit_status(
    primary: tuple[str, ...],
    compact: tuple[str, ...],
    *,
    minimum: str,
    columns: int | None,
    details: tuple[str, ...] = (),
) -> str:
    if columns is None:
        return " | ".join((*primary, *details))
    for candidate in (primary, compact, compact[:2], compact[:1], (minimum,)):
        text = " | ".join(candidate)
        if get_cwidth(text) <= columns:
            for detail in details:
                extended = text + " | " + detail
                if get_cwidth(extended) <= columns:
                    text = extended
            return text
    return "." * max(0, min(3, columns))


def status_text(state: ScreenState, now: float, *, no_animation: bool, columns: int | None = None) -> str:
    observed = state.observation
    if state.failure:
        if state.worker_exit_code is not None:
            return _fit_status(
                (f"Worker exited ({state.worker_exit_code}); Session closure unconfirmed",),
                (f"Worker lost ({state.worker_exit_code})",),
                minimum="Worker lost",
                columns=columns,
            )
        return _fit_status(
            ("Connection failed; cessation unknown", "owner supervision continues"),
            ("Connection lost", "cessation unknown"),
            minimum="Disconnected",
            columns=columns,
        )
    if state.closed:
        return _fit_status(("Session closed",), ("Closed",), minimum="Closed", columns=columns)
    if observed is None:
        return _fit_status(("Starting", "observation unavailable"), ("Starting",), minimum="Starting", columns=columns)
    active = observed.cell_id is not None
    execution_state = observed.execution.state if observed.execution is not None else observed.state
    if active and execution_state == "interruption_requested":
        label = "Interruption requested; cessation unconfirmed"
        compact_label, minimum = "Interrupt pending", "Pending"
    elif active and state.pending_interruption_cell_id == observed.cell_id:
        label = "Interrupt sent; awaiting owner"
        compact_label, minimum = "Interrupt sent", "Sent"
    elif active and execution_state == "queued":
        label = "Queued; awaiting execution"
        compact_label = minimum = "Queued"
    elif active:
        label = observed.execution.activity if observed.execution is not None else "Execution starting"
        compact_label = minimum = "Running" if execution_state == "running" else "Active"
    else:
        previous = execution_state
        label = f"Idle ({previous})" if previous in ("success", "failure", "cancelled") else "Idle"
        compact_label = minimum = previous.capitalize() if previous in ("success", "failure", "cancelled") else "Idle"
    elapsed = max(0, now - observed.started_at) if observed.started_at is not None else 0
    background = sum(item.state not in ("stopped", "failed") for item in observed.background_tuple)
    animation = "" if no_animation or not active else " " + "|/-\\"[int(now * 4) % 4]
    elapsed_fields = (f"{elapsed:.1f}s",) if active else ()
    return _fit_status(
        (label + animation, *elapsed_fields),
        (compact_label, *elapsed_fields),
        minimum=minimum,
        columns=columns,
        details=(f"background: {background}",) if background else (),
    )
