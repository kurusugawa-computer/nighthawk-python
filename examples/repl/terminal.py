"""Terminal launch glue; the spawned execution owner keeps the live Session."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .application import TerminalApplication
from .client import NativeOutput, WorkerClient, WorkerError
from .protocol import Completed, Failure, OutputRecord


async def run_headless(client: WorkerClient, source: TextIO, destination: TextIO, errors: TextIO) -> int:
    """Treat redirected input as one cell, preserving semantic text without screen control."""
    delivered: set[int] = set()

    def present(record) -> None:
        if isinstance(record, OutputRecord):
            if record.sequence in delivered:
                return
            delivered.add(record.sequence)
            if record.category == "semantic":
                destination.write(f"[{record.semantic_kind} cell={record.cell_id}] {record.text}\n")
                return
        target = errors if isinstance(record, (OutputRecord, NativeOutput)) and record.category == "stderr" else destination
        target.write(record.text)
        target.flush()

    try:
        await client.start()
        text = await asyncio.to_thread(source.read)
        status = 0
        owner_closed = False
        if text:
            response = await client.submit(text)
            if response.status != "submitted" or response.cell_id is None:
                errors.write(response.detail + "\n")
                return 1
            completion: Completed | None = None
            while completion is None:
                try:
                    event = await asyncio.wait_for(client.events.get(), 0.05)
                except TimeoutError:
                    event = None
                for record in client.read_presentation():
                    present(record)
                if isinstance(event, Completed) and event.cell_id == response.cell_id:
                    completion = event
                elif isinstance(event, Failure) and event.phase != "execution":
                    raise WorkerError(event.detail)
            for record in completion.output_tuple:
                present(record)
            if completion.output_incomplete:
                destination.write("[Completion presentation incomplete]\n")
            status = 0 if completion.status == "success" else 1
            owner_closed = completion.observation.execution is not None and completion.observation.execution.state == "closed"
        if not client.closed and not owner_closed:
            response = await client.close()
            if response.status != "closed":
                errors.write(f"Exit {response.status}: {response.detail}\n")
                errors.write("Input exhausted; disconnecting and supervising the owner until managed work ceases.\n")
                errors.flush()
                status = 1
                # EOF cannot supply a fresh exit decision. Disconnect activates
                # the owner's retained cleanup path without retrying refused exit.
                client.disconnect()
        await client.join()
        await client._join_readers()
        for record in client.read_presentation():
            present(record)
        return status
    finally:
        client.disconnect()
        await client.join()


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persistent Python/Natural terminal (Enter inserts newline; Escape-Enter submits).")
    parser.add_argument("--directory", type=Path, default=Path.cwd(), help="project directory (default: launch directory)")
    parser.add_argument("--execution-model", help="execute block model; provider construction remains lazy")
    parser.add_argument("--decision-model", help="decide block model; without one, decide blocks fail")
    parser.add_argument("--session-manager-factory", metavar="MODULE:FUNCTION", help="project SessionManager factory")
    parser.add_argument("--no-animation", action="store_true", help="use static activity indicators while elapsed time continues updating")
    options = parser.parse_args(arguments)
    client = WorkerClient(
        directory=options.directory,
        execution_model=options.execution_model,
        decision_model=options.decision_model,
        factory_reference=options.session_manager_factory,
    )
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return asyncio.run(run_headless(client, sys.stdin, sys.stdout, sys.stderr))
        return asyncio.run(TerminalApplication(client, no_animation=options.no_animation).run())
    except Exception as error:  # noqa: BLE001 - Report application failures at the terminal entry point.
        print(f"Nighthawk terminal: {error}", file=sys.stderr)
        return 1
    return 0
