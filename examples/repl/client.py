"""Asynchronous terminal peer with bounded presentation buffering and explicit replies."""

from __future__ import annotations

import asyncio
import codecs
import os
import socket
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Literal
from uuid import uuid4

from examples.repl.protocol import (
    EVENT_ADAPTER,
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
from nighthawk.session import InterruptExecutionRequest, OperatorRequest, OperatorResult
from nighthawk.session.controls import OperatorControl


@dataclass(frozen=True)
class NativeOutput:
    category: Literal["stdout", "stderr"]
    text: str
    phase: Literal["startup", "native/background"]
    cell_id: None = None


@dataclass(frozen=True)
class OutputGap:
    """Presentation is incomplete; this says nothing about authoritative run evidence."""

    text: str = "Output delivery incomplete: presentation records were discarded."
    incomplete: bool = True


PresentationRecord = OutputRecord | NativeOutput | OutputGap


class WorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerExited:
    """Parent-observed process death, independent of Session closure evidence."""

    exit_code: int


class WorkerClient(OperatorControl):
    def __init__(
        self,
        *,
        directory: Path,
        execution_model: str | None = None,
        decision_model: str | None = None,
        factory_reference: str | None = None,
        maximum_record_count: int = 1024,
    ) -> None:
        self.directory = directory
        self.execution_model = execution_model
        self.decision_model = decision_model
        self.factory_reference = factory_reference
        self.observation: Observation | None = None
        self.ready: Ready | None = None
        self.events: asyncio.Queue[Event | WorkerExited] = asyncio.Queue()
        self._request_id_to_future: dict[str, asyncio.Future[Response | OperationResponse]] = {}
        self._cell_id_to_completion: dict[str, Completed] = {}
        self._completion_changed = asyncio.Event()
        self._presentation: deque[PresentationRecord] = deque(maxlen=maximum_record_count)
        self._presentation_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._presentation_incomplete = False
        self._last_output_sequence = 0
        self._expected_output_sequence = 0
        self._control: Connection | None = None
        self._output: Connection | None = None
        self._failure: WorkerError | None = None
        self._closing = False
        self.closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready_future: asyncio.Future[Ready] | None = None
        self._thread_list: list[threading.Thread] = []
        self._control_finished = threading.Event()
        self.exit_code: int | None = None
        self._process_exited = asyncio.Event()
        self.process: subprocess.Popen[bytes] | None = None

    async def start(self) -> Ready:
        if self.process is not None:
            raise RuntimeError("Worker startup cannot be replayed")
        self._loop = asyncio.get_running_loop()
        self._ready_future = self._loop.create_future()
        parent_socket, worker_socket = socket.socketpair()
        self._control = Connection(parent_socket.detach())
        worker_control = Connection(worker_socket.detach())
        output_receiver, output_sender = os.pipe()
        self._output = Connection(output_receiver, writable=False)
        worker_output = Connection(output_sender, readable=False)
        output_read, output_write = os.pipe()
        error_read, error_write = os.pipe()
        command = [
            sys.executable,
            "-m",
            "examples.repl.worker",
            "--control-descriptor",
            str(worker_control.fileno()),
            "--output-descriptor",
            str(worker_output.fileno()),
            "--directory",
            str(self.directory.resolve()),
        ]
        if self.execution_model is not None:
            command.extend(("--execution-model", self.execution_model))
        if self.decision_model is not None:
            command.extend(("--decision-model", self.decision_model))
        if self.factory_reference is not None:
            command.extend(("--session-manager-factory", self.factory_reference))
        try:
            self.process = subprocess.Popen(  # noqa: ASYNC220 - Establish process ownership before allowing cancellation.
                command,
                cwd=Path(__file__).resolve().parents[2],
                pass_fds=(worker_control.fileno(), worker_output.fileno()),
                stdin=subprocess.DEVNULL,
                stdout=output_write,
                stderr=error_write,
                start_new_session=True,
            )
        except BaseException:
            self._control.close()
            self._output.close()
            os.close(output_read)
            os.close(error_read)
            raise
        finally:
            worker_control.close()
            worker_output.close()
            os.close(output_write)
            os.close(error_write)
        for target, arguments, name in (
            (self._read_control, (), "control"),
            (self._read_output, (), "output"),
            (self._read_native, (output_read, "stdout"), "stdout"),
            (self._read_native, (error_read, "stderr"), "stderr"),
            (self._watch_process, (), "process"),
        ):
            thread = threading.Thread(target=target, args=arguments, daemon=True, name=f"nighthawk-client-{name}")
            self._thread_list.append(thread)
            thread.start()
        try:
            return await self._ready_future
        except WorkerError:
            await self.join()
            await self._join_readers()
            self.disconnect()
            raise

    def _dispatch(self, event: Event | WorkerExited) -> None:
        if isinstance(event, WorkerExited):
            self.exit_code = event.exit_code
            self._process_exited.set()
            if not self.closed and self._failure is None:
                self._dispatch(Failure(phase="transport", detail=f"Execution owner exited ({event.exit_code}); Session closure unconfirmed."))
        observed = event if isinstance(event, Observation) else getattr(event, "observation", None)
        if observed is not None and (self.observation is None or observed.revision >= self.observation.revision):
            self.observation = observed
        if isinstance(event, (Response, Failure, Closed)) and event.output_sequence is not None:
            self._expected_output_sequence = max(self._expected_output_sequence, event.output_sequence)
        if isinstance(event, Ready):
            self.ready = event
            assert self._ready_future is not None
            if not self._ready_future.done():
                self._ready_future.set_result(event)
        elif isinstance(event, (Response, OperationResponse)):
            future = self._request_id_to_future.pop(event.request_id, None)
            if future is not None and not future.done():
                future.set_result(event)
        elif isinstance(event, Completed):
            self._cell_id_to_completion[event.cell_id] = event
            self._completion_changed.set()
        elif isinstance(event, Closed):
            self.closed = True
        elif isinstance(event, Failure) and event.phase != "execution":
            self._failure = WorkerError(f"{event.phase}: {event.detail}")
            if self._ready_future is not None and not self._ready_future.done():
                self._ready_future.set_exception(self._failure)
            for future in self._request_id_to_future.values():
                if not future.done():
                    future.set_exception(self._failure)
            self._request_id_to_future.clear()
            self._completion_changed.set()
        if isinstance(event, Response) and event.status == "closed":
            self.closed = True
        self.events.put_nowait(event)

    def _schedule(self, event: Event | WorkerExited) -> None:
        assert self._loop is not None
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._dispatch, event)

    def _read_control(self) -> None:
        assert self._control is not None
        try:
            while True:
                event = EVENT_ADAPTER.validate_json(self._control.recv_bytes())
                self._schedule(event)
                if (
                    isinstance(event, Closed)
                    or isinstance(event, Response)
                    and event.status == "closed"
                    or isinstance(event, Failure)
                    and event.phase == "startup"
                ):
                    return
        except (OSError, EOFError, ValueError, TypeError) as error:
            if not self._closing:
                self._schedule(Failure(phase="transport", detail=f"Execution owner connection lost; cessation unknown ({type(error).__name__})."))
        finally:
            self._control_finished.set()

    def _watch_process(self) -> None:
        assert self.process is not None
        exit_code = self.process.wait()
        # Give already-sent closure evidence a bounded chance to drain. An
        # inherited transport endpoint cannot conceal the owner's process death.
        self._control_finished.wait(0.2)
        self._schedule(WorkerExited(exit_code))

    def _append(self, record: PresentationRecord) -> None:
        with self._presentation_lock:
            if len(self._presentation) == self._presentation.maxlen:
                self._presentation_incomplete = True
            self._presentation.append(record)

    def _read_output(self) -> None:
        assert self._output is not None
        try:
            while True:
                event = EVENT_ADAPTER.validate_json(self._output.recv_bytes())
                if not isinstance(event, OutputRecord):
                    raise TypeError("Unexpected message on output channel")
                if event.sequence != self._last_output_sequence + 1:
                    self._append(OutputGap())
                self._last_output_sequence = event.sequence
                self._append(event)
        except (OSError, EOFError, ValueError, TypeError):
            return

    def _read_native(self, descriptor: int, category: Literal["stdout", "stderr"]) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while chunk := os.read(descriptor, 8192):
                text = decoder.decode(chunk)
                if text:
                    self._append(NativeOutput(category, text, "startup" if self.ready is None else "native/background"))
            text = decoder.decode(b"", final=True)
            if text:
                self._append(NativeOutput(category, text, "startup" if self.ready is None else "native/background"))
        finally:
            os.close(descriptor)

    def read_presentation(self) -> tuple[PresentationRecord, ...]:
        with self._presentation_lock:
            result = tuple(self._presentation)
            self._presentation.clear()
            if self._presentation_incomplete:
                result = (OutputGap(), *result)
                self._presentation_incomplete = False
            return result

    async def request(self, request: Request) -> Response | OperationResponse:
        if self._failure is not None:
            raise self._failure
        if self._control is None or self._closing:
            raise WorkerError("Execution owner is not connected")
        future: asyncio.Future[Response | OperationResponse] = asyncio.get_running_loop().create_future()
        self._request_id_to_future[request.request_id] = future
        # The application serializes sends. Offload even large input cells so pipe
        # backpressure cannot block the terminal event loop.
        try:
            await asyncio.to_thread(self._send, request)
            return await future
        except BaseException:
            self._request_id_to_future.pop(request.request_id, None)
            raise

    def _send(self, request: Request) -> None:
        assert self._control is not None
        with self._send_lock:
            self._control.send_bytes(request.model_dump_json().encode())

    async def submit(self, text: str, *, mode: Literal["python", "natural_language"] | None = None) -> Response:
        response = await self.request(SubmitRequest(request_id=uuid4().hex, text=text, mode=mode))
        assert isinstance(response, Response)
        return response

    async def interrupt(self, cell_id: str) -> Response:
        response = await self.request(InterruptRequest(request_id=uuid4().hex, cell_id=cell_id))
        assert isinstance(response, Response)
        return response

    async def observe(self) -> Response:
        response = await self.request(ControlRequest(request_id=uuid4().hex, operation="observe"))
        assert isinstance(response, Response)
        return response

    async def invoke(self, request: OperatorRequest) -> OperatorResult:
        target = self.observation.cell_id if isinstance(request, InterruptExecutionRequest) and self.observation is not None else None
        response = await self.request(OperationRequest(request_id=uuid4().hex, request=request, cell_id=target))
        if isinstance(response, Response):
            raise WorkerError(response.detail)
        return response.result

    async def wait(self, cell_id: str) -> Completed:
        while cell_id not in self._cell_id_to_completion:
            if self._failure is not None:
                raise self._failure
            self._completion_changed.clear()
            await self._completion_changed.wait()
        return self._cell_id_to_completion[cell_id]

    async def close(self) -> Response:
        response = await self.request(ControlRequest(request_id=uuid4().hex, operation="close"))
        assert isinstance(response, Response)
        if response.status == "closed":
            self.closed = True
            await self.join()
            await self._join_readers()
            self.disconnect()
        return response

    def disconnect(self) -> None:
        """Release transport resources; this is not evidence of Session closure."""

        self._closing = True
        if self._control is not None:
            # A blocked receiver keeps a Unix socket alive after close(fd).
            # Shutdown wakes both peers before releasing the descriptor.
            if os.name == "posix" and not self._control.closed:
                endpoint = socket.socket(fileno=self._control.fileno())
                try:
                    endpoint.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                finally:
                    endpoint.detach()
            self._control.close()
        if self._output is not None:
            self._output.close()

    async def join(self) -> None:
        if self.process is not None and self.process.pid is not None:
            await self._process_exited.wait()

    async def _join_readers(self) -> None:
        for thread in self._thread_list:
            await asyncio.to_thread(thread.join, 0.2)
        if self._last_output_sequence < self._expected_output_sequence:
            self._append(OutputGap())
