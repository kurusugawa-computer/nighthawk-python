"""Explicit trusted ownership registration; no job discovery or scheduling."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from nighthawk._identifiers import generate_ulid
from nighthawk.harness.ledger import RunEndEvent
from nighthawk.session._source import require_operator
from nighthawk.session.controls import OperatorControl
from nighthawk.session.errors import SessionClosedError
from nighthawk.session.operator_contract import BackgroundObservation, InterruptionResult

if TYPE_CHECKING:
    from nighthawk.session._session import Session


class ManagedBackgroundWork(Protocol):
    """Bounded host callbacks. A stopped/failed observation certifies actual cessation.

    Callbacks must not invoke an Agent or synchronously await their requesting
    cell. The adapter owns loop scheduling, joining, and truthful completion.
    Session replaces its work identity and preserves its first owner label.
    """

    def read(self) -> BackgroundObservation: ...

    def request_interrupt(self) -> InterruptionResult: ...


@dataclass
class _BackgroundEntry:
    work: ManagedBackgroundWork
    observation: BackgroundObservation


class SessionBackground(OperatorControl):
    def __init__(self, session: Session) -> None:
        self._session = session
        self._lock = threading.RLock()
        self._work_id_to_entry: dict[str, _BackgroundEntry] = {}
        self._revision = 0

    def register(self, work: ManagedBackgroundWork) -> str:
        require_operator("background_registration")
        self._session._require_open()
        with self._lock:
            if self._session._closing:
                raise SessionClosedError("Session close is pending")
            for work_id, entry in self._work_id_to_entry.items():
                if entry.work is work:
                    return work_id
        # Owners are trusted, but their callbacks run outside the ownership lock.
        identity = generate_ulid()
        try:
            observed = replace(work.read(), work_id=identity)
        except Exception as error:  # noqa: BLE001 - An owner callback failure makes its state unknown, not stopped.
            observed = BackgroundObservation(
                identity,
                type.__dict__["__name__"].__get__(type(work)),
                "Observation unavailable",
                "unknown",
                time.time(),
                True,
                reason=type(error).__name__,
            )
        with self._lock:
            if self._session._closing:
                raise SessionClosedError("Session close is pending")
            for work_id, entry in self._work_id_to_entry.items():
                if entry.work is work:
                    return work_id
            self._work_id_to_entry[identity] = _BackgroundEntry(work, observed)
            self._revision += 1
        return identity

    def _is_registered(self, value: object) -> bool:
        with self._lock:
            return any(entry.work is value for entry in self._work_id_to_entry.values())

    def list(self) -> tuple[BackgroundObservation, ...]:
        require_operator("background_observation")
        with self._lock:
            entries = tuple(self._work_id_to_entry.items())
        observations: list[BackgroundObservation] = []
        for work_id, entry in entries:
            previous = entry.observation
            if previous.state in ("stopped", "failed"):
                observations.append(previous)
                continue
            try:
                observed = replace(entry.work.read(), work_id=work_id, owner=previous.owner)
            except Exception as error:  # noqa: BLE001 - An owner callback failure makes its state unknown, not stopped.
                observed = replace(previous, state="unknown", observed_at=time.time(), reason=f"Owner observation failed: {type(error).__name__}")
            with self._lock:
                entry.observation = observed
            observations.append(observed)
        execution = self._session._active_execution
        if execution is not None and execution._body_unwound:
            for handle in execution._handle_list:
                if not handle.ledger.select(RunEndEvent):
                    observations.append(
                        BackgroundObservation(
                            f"run:{handle.run_id}",
                            "Session run cleanup",
                            "Retained run descendants have not ceased",
                            "interruption_requested" if execution._interrupted else "running",
                            time.time(),
                            True,
                            cell_id=execution.cell_id,
                            run_id=handle.run_id,
                        )
                    )
        return tuple(observations)

    def interrupt(self, work_id: str) -> InterruptionResult:
        require_operator("background_interruption")
        observations = self.list()
        observed = next((item for item in observations if item.work_id == work_id), None)
        if observed is None:
            return InterruptionResult("missing", work_id)
        if observed.state in ("stopped", "failed"):
            return InterruptionResult("already_finished", work_id)
        if not observed.interruption_available:
            return InterruptionResult("unavailable", work_id, "The owner has no interruption operation")
        with self._lock:
            entry = self._work_id_to_entry.get(work_id)
        if entry is not None:
            try:
                result = replace(entry.work.request_interrupt(), target_id=work_id)
            except Exception as error:  # noqa: BLE001 - An owner callback failure makes its state unknown, not stopped.
                return InterruptionResult("unavailable", work_id, f"Owner interruption failed: {type(error).__name__}")
            if result.status == "requested":
                with self._lock:
                    entry.observation = replace(entry.observation, state="interruption_requested", observed_at=time.time())
            return result
        execution = self._session._active_execution
        if execution is not None:
            for handle in execution._handle_list:
                if f"run:{handle.run_id}" == work_id:
                    handle.terminate("operator background interruption")
                    return InterruptionResult("requested", work_id, "Cessation remains unconfirmed")
        return InterruptionResult("missing", work_id)

    def _blockers(self) -> tuple[BackgroundObservation, ...]:
        return tuple(item for item in self.list() if item.state not in ("stopped", "failed"))
