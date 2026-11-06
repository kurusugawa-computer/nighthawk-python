"""Deterministic application peer using a test-owned Session and caller event loop."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from examples.repl.client import WorkerClient
from examples.repl.protocol import Closed, Completed, Observation, OutputRecord, Ready, Response
from nighthawk.agent.session import CellInput, CellMode, ExitResult, LocalPage, LocalQuery, OperatorRequest, OperatorResult, Session, create_session


async def until(predicate: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


class ApplicationClient(WorkerClient):
    def __init__(self, session: Session | None = None) -> None:
        super().__init__(directory=Path.cwd())
        self.session = session or create_session(execution_model="openai-responses:gpt-6-luna")
        self.submissions: list[CellInput] = []
        self._revision = 0
        self._sequence = 0

    def snapshot(self, cell_id: str | None = None) -> Observation:
        self._revision += 1
        execution = self.session.execution.read()
        page = (
            self.session.locals.read(LocalQuery(revision=execution.revision))
            if execution.state != "closed"
            else LocalPage((), time.time(), execution.revision)
        )
        return Observation(
            revision=self._revision,
            observed_at=time.time(),
            cell_id=cell_id,
            state=execution.state,
            started_at=execution.started_at,
            execution=execution,
            locals=page,
            background_tuple=self.session.background.list(),
            binding_name_tuple=tuple(binding.name for binding in page.binding_tuple),
            operation_tuple=self.session.operations.list(),
            history=self.session.history.read() if execution.state != "closed" else None,
        )

    async def start(self) -> Ready:
        self.ready = Ready(process_id=0, session_id=self.session.session_id, observation=self.snapshot())
        self.events.put_nowait(self.ready)
        return self.ready

    async def submit(self, text: str, *, mode: CellMode | None = None) -> Response:
        cell = CellInput(text, mode)
        self.submissions.append(cell)
        execution = self.session.submit(cell)
        response = Response(
            request_id=str(len(self.submissions)), status="submitted", cell_id=execution.cell_id, observation=self.snapshot(execution.cell_id)
        )

        async def drive() -> None:
            outcome = await execution.wait_async()
            records = []
            for record in outcome.output_tuple:
                self._sequence += 1
                records.append(
                    OutputRecord(sequence=self._sequence, cell_id=record.cell_id, category="semantic", semantic_kind=record.kind, text=record.text)
                )
            self.events.put_nowait(
                Completed(cell_id=execution.cell_id, status=outcome.status, observation=self.snapshot(), output_tuple=tuple(records))
            )
            if self.session.execution.read().state == "closed":
                self.events.put_nowait(Closed(output_sequence=self._sequence))

        asyncio.create_task(drive())
        return response

    async def invoke(self, request: OperatorRequest) -> OperatorResult:
        result = self.session.operations.invoke(request)
        if isinstance(result, ExitResult) and result.status == "closed":
            self.events.put_nowait(Closed(output_sequence=self._sequence))
        return result

    async def join(self) -> None:
        pass

    async def _join_readers(self) -> None:
        pass

    def disconnect(self) -> None:
        pass
