from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from nighthawk.harness.errors import RunTerminationError
from nighthawk.harness.ledger import RunLedger

if TYPE_CHECKING:
    from nighthawk.harness._host import Host


class RunHandle:
    """Platform-side handle to one run: its ledger and host-directed termination."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._host: Host | None = None
        self._ledger: RunLedger | None = None
        self._termination_requested = False
        self._termination_reason: str | None = None

    def _attach(self, host: Host) -> None:
        with self._lock:
            self._host = host
            self._ledger = host.ledger
            termination_requested = self._termination_requested
            termination_reason = self._termination_reason
        if termination_requested:
            host.terminate_run(termination_reason or "host directed")

    @property
    def ledger(self) -> RunLedger:
        with self._lock:
            if self._ledger is None:
                raise RuntimeError("the run has not started")
            return self._ledger

    @property
    def run_id(self) -> str | None:
        with self._lock:
            return self._host.run_id if self._host is not None else None

    def terminate(self, reason: str = "host directed") -> bool:
        """Direct the host to terminate the run and return whether cessation was established.

        Every active interior branch, including the scaffold composition's own
        execution while it is running, is presented to the platform termination
        mechanism. ``True`` means the platform established that no interior
        branch can execute or submit operations any more and the host recorded
        the run end; the pending ``Runner.run`` call then raises a typed run
        failure. ``False`` means the platform did not establish cessation: no
        run-end event exists, the host refuses every further interior operation
        at its mediation seams, and the run ends only when the scaffold reaches
        the run-end seam on its own. A request made before the run starts ends
        the run before any interior execution begins.
        """

        with self._lock:
            self._termination_requested = True
            self._termination_reason = reason
            host = self._host
        if host is None:
            return True
        return host.terminate_run(reason)

    def finish_pending(self) -> None:
        """Finish only cleanup after the body unwound with unresolved cessation.

        This never drives interior execution. The owner must keep its original
        driver until the body has unwound; a Session may then reattach here.
        """

        with self._lock:
            host = self._host
        if host is None or host.run_root_active or not host.ended:
            raise RuntimeError("cleanup requires an unwound run body")
        if not host.terminate_run("finish pending run cleanup"):
            remaining = host.nonterminal_dispatch_node_id_tuple()
            event_id = host.record_anomaly(
                "branch_cessation_unestablished",
                node_id=host.run_root.node_id,
                detail=f"cleanup still awaits cessation of: {', '.join(remaining)}",
            )
            raise RunTerminationError(run_id=host.run_id, anomaly_event_id=event_id, nonterminal_node_id_tuple=remaining)

    def pending_branch_loop_tuple(self) -> tuple[asyncio.AbstractEventLoop, ...]:
        with self._lock:
            host = self._host
        return () if host is None else host.pending_branch_loop_tuple()
