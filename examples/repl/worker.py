"""Main-thread Session owner for the terminal's single execution subprocess.

Only the owner thread touches Session. Receiver and sender threads move immutable
messages; a separate output connection cannot hold interruption behind a flood.
"""

from __future__ import annotations

import asyncio
import contextvars
import io
import os
import queue
import signal
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from multiprocessing.connection import Connection
from types import FrameType, ModuleType
from typing import Any, Literal

from examples.repl.protocol import (
    REQUEST_ADAPTER,
    Closed,
    Completed,
    ControlRequest,
    Event,
    Failure,
    InterruptRequest,
    Observation,
    OperationRequest,
    OperationResponse,
    OutputRecord,
    Ready,
    Request,
    Response,
    SubmitRequest,
)


class _InterruptDeferral:
    def __init__(self) -> None:
        self._depth = 0
        self._pending = False

    @contextmanager
    def hold(self) -> Generator[None]:
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0 and self._pending:
                self._pending = False
                raise KeyboardInterrupt()

    def interrupt(self) -> None:
        if self._depth:
            self._pending = True
        else:
            raise KeyboardInterrupt()


class OutputDelivery:
    """Bound presentation storage; sequence gaps disclose discarded observations.

    No authoritative Session or ledger record is stored here. Sending is confined
    to a daemon thread, so a blocked reader cannot block recording cell cessation.
    """

    def __init__(self, connection: Connection, interruption: _InterruptDeferral, maximum_record_count: int = 256) -> None:
        self.connection = connection
        self._interruption = interruption
        self.records: deque[OutputRecord] = deque(maxlen=maximum_record_count)
        self.sequence = 0
        self._sending = False
        self._lock = threading.Lock()
        threading.Thread(target=self._send, daemon=True, name="nighthawk-output-sender").start()

    def record(self, *, cell_id: str | None, category: Literal["stdout", "stderr", "semantic"], text: str, **metadata: Any) -> OutputRecord:
        with self._interruption.hold(), self._lock:
            self.sequence += 1
            record = OutputRecord(sequence=self.sequence, cell_id=cell_id, category=category, text=text, **metadata)
            self.records.append(record)
        return record

    def _send(self) -> None:
        try:
            while True:
                # Defer signal exceptions through lock entry as well as its body;
                # Python 3.14 can deliver them after __enter__ acquires the lock.
                with self._interruption.hold(), self._lock:
                    record = self.records.popleft() if self.records else None
                    self._sending = record is not None
                if record is None:
                    time.sleep(0.005)
                else:
                    self.connection.send_bytes(record.model_dump_json().encode())
        except (OSError, EOFError):
            return

    def flush(self, timeout: float = 0.2) -> None:
        """Allow final presentation delivery briefly, without waiting on a full pipe."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._interruption.hold(), self._lock:
                if not self.records and not self._sending:
                    return
            time.sleep(0.005)


class ControlDelivery:
    """Reliable replies plus one coalesced observation, independent of stream volume."""

    def __init__(self, interruption: _InterruptDeferral) -> None:
        self._interruption = interruption
        self._replies: queue.SimpleQueue[Event] = queue.SimpleQueue()
        self._observation: Observation | None = None
        self._lock = threading.Lock()

    def put(self, event: Event) -> None:
        if isinstance(event, Observation):
            with self._interruption.hold(), self._lock:
                self._observation = event
        else:
            self._replies.put(event)

    def get(self) -> Event:
        while True:
            try:
                return self._replies.get(timeout=0.05)
            except queue.Empty:
                with self._interruption.hold(), self._lock:
                    observed, self._observation = self._observation, None
                if observed is not None:
                    return observed


class RecordingStream(io.TextIOBase):
    def __init__(self, delivery: OutputDelivery, category: Literal["stdout", "stderr"], current_cell: Callable[[], str | None]) -> None:
        self.delivery = delivery
        self.category: Literal["stdout", "stderr"] = category
        self.current_cell = current_cell

    encoding = "utf-8"

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:
        return 1 if self.category == "stdout" else 2

    def write(self, text: str) -> int:
        cell_id = self.current_cell() if threading.current_thread() is threading.main_thread() else None
        for i in range(0, len(text), 6000):
            self.delivery.record(cell_id=cell_id, category=self.category, text=text[i : i + 6000])
        return len(text)

    def flush(self) -> None:
        pass


async def _run(
    control: Connection,
    output: Connection,
    directory: str,
    execution_model: str | None,
    decision_model: str | None,
    factory_reference: str | None,
) -> None:
    # Imports happen after native descriptors are redirected, including factory imports.
    from pathlib import Path

    from examples.repl.configuration import create_terminal_session, terminal_environment
    from nighthawk.harness.errors import RunTerminationError
    from nighthawk.session import (
        BindingMutationResult,
        CellExecution,
        CellInput,
        CellOutput,
        DeleteLocalRequest,
        ExitResult,
        InterruptExecutionRequest,
        InterruptionResult,
        LocalPage,
        RegisterBackgroundRequest,
        RenameLocalRequest,
        SessionWaitContextError,
    )

    interruption = _InterruptDeferral()
    outgoing = ControlDelivery(interruption)
    delivery = OutputDelivery(output, interruption)
    execution: CellExecution | None = None
    driver: asyncio.Task[None] | None = None
    active_cell_id: str | None = None
    started_at: float | None = None
    revision = 0
    last_observation: Observation | None = None
    interrupted_cell_id_queue: deque[str] = deque()
    loop = asyncio.get_running_loop()
    operator_context = contextvars.copy_context()
    shutdown = asyncio.Event()
    incoming: asyncio.Queue[tuple[Request, str | None]] = asyncio.Queue()

    def send_control() -> None:
        try:
            while True:
                event = outgoing.get()
                control.send_bytes(event.model_dump_json().encode())
                if isinstance(event, Closed) or isinstance(event, Response) and event.status == "closed":
                    loop.call_soon_threadsafe(shutdown.set)
                    return
        except (OSError, EOFError):
            loop.call_soon_threadsafe(shutdown.set)

    def receive_control() -> None:
        try:
            while True:
                request = REQUEST_ADAPTER.validate_json(control.recv_bytes())
                observed_cell_id = active_cell_id
                if isinstance(request, OperationRequest) and isinstance(request.request, RegisterBackgroundRequest) and observed_cell_id is not None:
                    outgoing.put(Response(request_id=request.request_id, status="busy", detail="The namespace is busy; registration was not queued"))
                    continue
                if (
                    isinstance(request, OperationRequest)
                    and isinstance(request.request, (DeleteLocalRequest, RenameLocalRequest))
                    and observed_cell_id is not None
                ):
                    mutation = request.request
                    outgoing.put(
                        OperationResponse(
                            request_id=request.request_id,
                            result=BindingMutationResult(
                                "busy",
                                mutation.name,
                                mutation.new_name if isinstance(mutation, RenameLocalRequest) else None,
                                last_observation.locals.revision if last_observation is not None and last_observation.locals is not None else 0,
                                "The namespace is busy; no operation was queued",
                            ),
                            observation=last_observation,
                        )
                    )
                    continue
                interruption_target = (
                    request.cell_id
                    if isinstance(request, InterruptRequest)
                    or isinstance(request, OperationRequest)
                    and isinstance(request.request, InterruptExecutionRequest)
                    else None
                )
                if interruption_target is not None and interruption_target == observed_cell_id:
                    interrupted_cell_id_queue.append(interruption_target)
                    os.kill(os.getpid(), signal.SIGUSR1)
                loop.call_soon_threadsafe(incoming.put_nowait, (request, observed_cell_id))
        except (OSError, EOFError, ValueError):
            if active_cell_id is not None:
                interrupted_cell_id_queue.append(active_cell_id)
                os.kill(os.getpid(), signal.SIGUSR1)
            loop.call_soon_threadsafe(shutdown.set)

    def interrupt(signum: int, frame: FrameType | None) -> None:
        targeted = False
        while interrupted_cell_id_queue:
            target = interrupted_cell_id_queue.popleft()
            if execution is not None and execution.outcome is None and target == active_cell_id == execution.cell_id:
                operator_context.copy().run(execution.request_interrupt)
                targeted = True
        if targeted and driver is not None and asyncio.current_task() is driver:
            interruption.interrupt()

    if os.name != "posix":
        raise RuntimeError("The terminal execution owner requires POSIX signals; embedding Session remains platform independent")
    signal.signal(signal.SIGUSR1, interrupt)
    threading.Thread(target=send_control, daemon=True, name="nighthawk-control-sender").start()
    threading.Thread(target=receive_control, daemon=True, name="nighthawk-control-receiver").start()

    def current_output_cell() -> str | None:
        # Sharing the owner thread does not make a task part of the foreground
        # cell. Unowned task/callback output must not inherit its identity.
        return active_cell_id if driver is not None and asyncio.current_task(loop=loop) is driver else None

    sys.stdout = RecordingStream(delivery, "stdout", current_output_cell)
    sys.stderr = RecordingStream(delivery, "stderr", current_output_cell)

    with terminal_environment(Path(directory)):
        try:
            session = create_terminal_session(execution_model=execution_model, decision_model=decision_model, factory_reference=factory_reference)
        except BaseException as error:  # noqa: BLE001 - Report host operation failures through the control protocol.
            # Startup must flush its failure before exiting; no live Session exists.
            await asyncio.to_thread(delivery.flush)
            control.send_bytes(
                Failure(phase="startup", detail=f"{type(error).__name__}: {error}", output_sequence=delivery.sequence).model_dump_json().encode()
            )
            return
        module_name = session.binding_name_to_value.get("__name__")
        module = sys.modules.get(module_name) if isinstance(module_name, str) else None
        if isinstance(module, ModuleType):
            sys.modules["__main__"] = module
            session.binding_name_to_value["__name__"] = "__main__"

        def observe() -> Observation:
            nonlocal revision, last_observation
            revision += 1
            # This function only runs at serialized boundaries on the worker main
            # thread, including when a cell has yielded to its owning event loop.
            if not session._disposed:
                session._observe()
            current = session.execution.read()
            if current.state == "closed":
                local_page = LocalPage((), current.observed_at, current.revision)
                history = None
            else:
                local_page = session.locals._snapshot
                first = session.history.read()
                history = session.history.read(starting_index=max(0, first.horizon - 20), horizon=first.horizon)
            last_observation = Observation(
                revision=revision,
                observed_at=time.time(),
                cell_id=active_cell_id,
                state=execution.status if execution is not None else "idle",
                started_at=started_at,
                run_id_tuple=execution.run_id_tuple if execution is not None else (),
                binding_name_tuple=tuple(item.name for item in local_page.binding_tuple),
                execution=current,
                locals=local_page,
                background_tuple=session.background.list(),
                operation_tuple=tuple(
                    replace(item, available=False, unavailable_reason="The namespace is busy; mutations are not queued")
                    if active_cell_id is not None and item.kind == "mutation"
                    else item
                    for item in session.operations.list()
                ),
                history=history,
            )
            return last_observation

        async def drive(current: CellExecution) -> None:
            nonlocal active_cell_id, started_at
            # wait_async yields after establishing its running observation and
            # before entering user Python, including a silent synchronous cell.
            loop.call_soon(lambda: outgoing.put(observe()))
            output_index = 0
            semantic_output_list: deque[OutputRecord] = deque()
            output_incomplete = False

            def drain(record: CellOutput | None = None) -> None:
                nonlocal output_index, output_incomplete
                while True:
                    page = current.read_output(starting_index=output_index)
                    for item in page.output_tuple:
                        semantic_output_list.append(
                            delivery.record(
                                cell_id=item.cell_id,
                                category="semantic",
                                text=item.text,
                                semantic_kind=item.kind,
                                run_id=item.run_id,
                                event_id=item.event_id,
                                incomplete=item.incomplete,
                                evidence_reference_tuple=item.evidence_reference_tuple,
                            )
                        )
                        while len(semantic_output_list) > 20 or sum(len(record.text) for record in semantic_output_list) > 6000:
                            semantic_output_list.popleft()
                            output_incomplete = True
                    output_index += len(page.output_tuple)
                    if page.next_starting_index is None:
                        break
                outgoing.put(observe())

            while current.outcome is None:
                try:
                    await current.wait_async(on_output=lambda record: operator_context.copy().run(drain, record))
                except (KeyboardInterrupt, asyncio.CancelledError):
                    pass
                except (RunTerminationError, SessionWaitContextError) as error:
                    outgoing.put(Failure(phase="execution", detail=str(error)))
                finally:
                    drain()
                if current.outcome is None:
                    await asyncio.sleep(0.1)
            active_cell_id = None
            started_at = None
            outgoing.put(
                Completed(
                    cell_id=current.cell_id,
                    status=current.outcome.status,
                    observation=observe(),
                    output_tuple=tuple(semantic_output_list),
                    output_incomplete=output_incomplete,
                )
            )
            if session.execution.read().state == "closed":
                await asyncio.to_thread(delivery.flush)
                outgoing.put(Closed(output_sequence=delivery.sequence))

        outgoing.put(Ready(process_id=os.getpid(), session_id=session.session_id, observation=observe()))
        try:
            while not shutdown.is_set():
                try:
                    request, received_cell_id = await asyncio.wait_for(incoming.get(), timeout=0.1)
                except TimeoutError:
                    if not shutdown.is_set() and (active_cell_id is not None or session.background._work_id_to_entry):
                        outgoing.put(observe())
                    continue
                if isinstance(request, SubmitRequest):
                    if received_cell_id is not None or active_cell_id is not None:
                        outgoing.put(
                            Response(request_id=request.request_id, status="busy", detail="The preceding cell is unfinished; input was not queued.")
                        )
                        continue
                    try:
                        execution = session.submit(CellInput(request.text, mode=request.mode))
                        active_cell_id = execution.cell_id
                        started_at = time.time()
                        outgoing.put(Response(request_id=request.request_id, status="submitted", cell_id=active_cell_id, observation=observe()))
                        driver = asyncio.create_task(drive(execution))
                    except Exception as error:  # noqa: BLE001 - Report host operation failures through the control protocol.
                        outgoing.put(Response(request_id=request.request_id, status="failed", detail=f"{type(error).__name__}: {error}"))
                elif isinstance(request, InterruptRequest):
                    if execution is not None and request.cell_id == execution.cell_id:
                        # The receiver already queued the targeted signal.
                        # Repeating cancellation here can interrupt cleanup.
                        status = "requested" if execution.outcome is None else "finished"
                    else:
                        status = "missing"
                    outgoing.put(Response(request_id=request.request_id, status=status, cell_id=request.cell_id, observation=observe()))
                elif isinstance(request, ControlRequest):
                    if request.operation == "observe":
                        outgoing.put(Response(request_id=request.request_id, status="observed", observation=observe()))
                    else:
                        result = session.exit()
                        if result.status == "closed":
                            await asyncio.to_thread(delivery.flush)
                        outgoing.put(
                            Response(request_id=request.request_id, status=result.status, detail=result.reason, output_sequence=delivery.sequence)
                        )
                elif isinstance(request, OperationRequest):
                    try:
                        if isinstance(request.request, InterruptExecutionRequest):
                            # Both transport routes use the same signal handler,
                            # including while synchronous Python owns the thread.
                            requested = request.cell_id is not None and request.cell_id == active_cell_id
                            result = InterruptionResult(
                                "requested" if requested else "already_finished",
                                request.cell_id,
                                "A cancellation request does not establish cessation" if requested else "",
                            )
                        else:
                            result = session.operations.invoke(request.request)
                        outgoing.put(OperationResponse(request_id=request.request_id, result=result, observation=observe()))
                        if isinstance(result, ExitResult) and result.status == "closed":
                            await asyncio.to_thread(delivery.flush)
                            outgoing.put(Closed(output_sequence=delivery.sequence))
                    except Exception as error:  # noqa: BLE001 - Report host operation failures through the control protocol.
                        outgoing.put(Response(request_id=request.request_id, status="failed", detail=f"{type(error).__name__}: {error}"))
        finally:
            if execution is not None and execution.outcome is None:
                execution.request_interrupt()
                if driver is not None:
                    await driver
            # Losing presentation is not permission to abandon registered owners.
            # Keep their event loop alive until they independently certify cessation.
            # Do not send a closed acknowledgement or implicitly stop all work.
            while session.background._blockers():
                await asyncio.sleep(0.1)
            session.close()


def main() -> None:
    """Own explicitly inherited channels; Python objects never cross the process boundary."""

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--control-descriptor", type=int, required=True)
    parser.add_argument("--output-descriptor", type=int, required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--execution-model")
    parser.add_argument("--decision-model")
    parser.add_argument("--session-manager-factory")
    options = parser.parse_args()
    control = Connection(options.control_descriptor)
    output = Connection(options.output_descriptor, readable=False)
    # Child programs started explicitly by operator Python must not retain the
    # terminal's control channels after exec.
    os.set_inheritable(control.fileno(), False)
    os.set_inheritable(output.fileno(), False)
    try:
        asyncio.run(_run(control, output, options.directory, options.execution_model, options.decision_model, options.session_manager_factory))
    finally:
        control.close()
        output.close()


if __name__ == "__main__":
    main()
