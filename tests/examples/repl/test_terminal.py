from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from examples.repl.application import TerminalApplication
from examples.repl.client import NativeOutput, WorkerClient
from examples.repl.configuration import create_terminal_session, terminal_environment
from examples.repl.protocol import OutputRecord
from nighthawk.parts.conversation import ConversationInput, ConversationResult, UserFacingBlock
from nighthawk.session import CellInput, create_session
from tests.examples.repl.helpers import ApplicationClient, execute, output_until, until
from tests.support.session import configured


@pytest.mark.skipif(os.name != "posix", reason="Execution signal ownership requires POSIX")
async def test_terminal_execution_captures_streams_separately_and_restores_handlers(tmp_path: Path) -> None:
    client = WorkerClient(directory=tmp_path)
    previous = (sys.stdout, sys.stderr, signal.getsignal(signal.SIGINT), os.getcwd(), tuple(sys.path))
    try:
        await client.start()
        result = await execute(
            client, "import sys, asyncio\nawait asyncio.sleep(0)\nprint('stdout observation')\nprint('stderr observation', file=sys.stderr)\n42"
        )
        records = await output_until(client, "stderr observation")
        assert "42" in result
        assert {record.category for record in records if isinstance(record, (NativeOutput, OutputRecord))} >= {"stdout", "stderr"}
        assert previous == (sys.stdout, sys.stderr, signal.getsignal(signal.SIGINT), os.getcwd(), tuple(sys.path))
        await client.close()
    finally:
        if client.process is not None and client.process.poll() is None:
            client.process.kill()
            await client.join()
        client.disconnect()


async def test_prompt_bindings_multiline_clear_and_empty_exit() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    client = ApplicationClient(session)
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        task = asyncio.create_task(interface.run())
        await until(lambda: interface.application.is_running)
        pipe.send_text("discard this draft\x03! values = [1, 2]\rvalues.append(3)\rvalues\x1b\r")
        await until(lambda: len(client.submissions) == 1 and not interface.state.busy)
        assert session.binding_name_to_value["values"] == [1, 2, 3]
        assert "[1, 2, 3]" in interface.state.transcript
        inputs = [record.text for record in session.history.read().record_tuple if record.kind == "input"]
        assert len(inputs) == 1 and "discard" not in inputs[0]
        pipe.send_text("\x04")
        await asyncio.wait_for(task, 5)


def test_factory_environment_restores_process_after_failed_startup(tmp_path: Path) -> None:
    previous = (os.getcwd(), tuple(sys.path), sys.modules["__main__"])
    (tmp_path / "failing_session_factory.py").write_text(
        "def build(*, execution_model=None, decision_model=None):\n    raise ValueError('factory failed')\n"
    )
    with pytest.raises(ValueError, match="factory failed"), terminal_environment(tmp_path):
        create_terminal_session(factory_reference="failing_session_factory:build")
    assert previous == (os.getcwd(), tuple(sys.path), sys.modules["__main__"])


async def test_escape_n_preserves_literal_leading_python_marker() -> None:
    received: list[str] = []

    async def body(value: ConversationInput) -> ConversationResult:
        received.append(value.task)
        return ConversationResult((UserFacingBlock("answer", "literal input received"),))

    session = configured(body).create_session()
    client = ApplicationClient(session)
    with create_pipe_input() as pipe:
        interface = TerminalApplication(client, input=pipe, output=DummyOutput())
        task = asyncio.create_task(interface.run())
        await until(lambda: interface.application.is_running)
        pipe.send_text("> quoted natural language\x1bn")
        await until(lambda: len(client.submissions) == 1 and not interface.state.busy)
        assert received == ["> quoted natural language"]
        pipe.send_text("\x04")
        await asyncio.wait_for(task, 5)


def test_factory_is_called_once_with_model_and_returns_one_session(tmp_path: Path) -> None:
    (tmp_path / "project_session_factory.py").write_text(
        "from nighthawk.session import SessionManager\n"
        "from nighthawk.harness.runner import Runner\n"
        "from nighthawk.harness.perimeter import PerimeterPolicy\n"
        "from nighthawk.harness.resources import bounded_resource_allocation\n"
        "class Executor:\n"
        "    admits_executor_steps = True\n"
        "    def run_step(self, **options):\n"
        "        raise AssertionError('provider must remain unused')\n"
        "calls = []\n"
        "def build(*, execution_model=None, decision_model=None):\n"
        "    calls.append((execution_model, decision_model))\n"
        "    return SessionManager(runner=Runner(Executor(), PerimeterPolicy.NONE, lambda identity: bounded_resource_allocation()))\n"
    )
    with terminal_environment(tmp_path):
        session = create_terminal_session(
            execution_model="selected:execution", decision_model="selected:decision", factory_reference="project_session_factory:build"
        )
        try:
            assert session.submit(CellInput("> import project_session_factory\nproject_session_factory.calls")).wait().status == "success"
            assert sys.modules["project_session_factory"].calls == [("selected:execution", "selected:decision")]  # type: ignore[attr-defined]
        finally:
            session.close()


@pytest.mark.skipif(os.name != "posix", reason="Execution owner currently requires POSIX")
async def test_headless_presentation_preserves_streams_without_screen_control(tmp_path: Path) -> None:
    import io

    from examples.repl.terminal import run_headless

    client = WorkerClient(directory=tmp_path)
    destination, errors = io.StringIO(), io.StringIO()
    status = await asyncio.wait_for(
        run_headless(client, io.StringIO("> import sys\nprint('日本語')\nprint('diagnostic', file=sys.stderr)\n42"), destination, errors), 10
    )
    assert status == 0
    assert "日本語" in destination.getvalue() and "] 42" in destination.getvalue()
    assert "diagnostic" in errors.getvalue() and "diagnostic" not in destination.getvalue()
    assert "\x1b" not in destination.getvalue()
    assert client.process is not None and client.process.poll() is not None


@pytest.mark.skipif(os.name != "posix", reason="Execution owner currently requires POSIX")
async def test_headless_refusal_disconnects_and_reaps_after_confirmed_background_cessation(tmp_path: Path) -> None:
    import io

    from examples.repl.terminal import run_headless

    client = WorkerClient(directory=tmp_path)
    destination, errors = io.StringIO(), io.StringIO()
    source = io.StringIO(
        """> import asyncio, time
from pathlib import Path
from nighthawk.session import BackgroundObservation, InterruptionResult
async def owned_work():
    while not Path('release').exists():
        await asyncio.sleep(0.01)
    assert not session._closing and not session._exit_requested
    Path('ceased').write_text('confirmed')
task = asyncio.create_task(owned_work())
class Owner:
    def read(self):
        return BackgroundObservation('ignored', 'fixture', 'owned task', 'stopped' if task.done() else 'running', time.time(), True)
    def request_interrupt(self):
        Path('interrupted').write_text('unexpected')
        return InterruptionResult('requested', 'ignored')
session.background.register(Owner())
"""
    )
    operation = asyncio.create_task(run_headless(client, source, destination, errors))
    try:
        await until(lambda: "Exit refused" in errors.getvalue(), timeout=15)
        assert not operation.done()
        assert client.process is not None and client.process.poll() is None
        (tmp_path / "release").write_text("release")
        assert await asyncio.wait_for(operation, 5) == 1
        assert (tmp_path / "ceased").read_text() == "confirmed"
        assert not (tmp_path / "interrupted").exists()
        assert not client.closed
        assert client.process.poll() is not None and client.process.returncode == 0
        assert "disconnecting and supervising" in errors.getvalue()
    finally:
        (tmp_path / "release").write_text("release")
        client.disconnect()
        if not operation.done():
            operation.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(operation, return_exceptions=True), 5)
        finally:
            if client.process is not None and client.process.poll() is None:
                client.process.kill()
                await client.join()
