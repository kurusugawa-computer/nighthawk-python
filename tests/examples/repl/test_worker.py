from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from examples.repl.client import NativeOutput, OutputGap, WorkerClient, WorkerError
from examples.repl.protocol import OutputRecord
from tests.examples.repl.helpers import execute, output_until

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Worker signal interruption currently requires POSIX")


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[WorkerClient]:
    peer = WorkerClient(directory=tmp_path, maximum_record_count=64)
    try:
        await asyncio.wait_for(peer.start(), 15)
        yield peer
    finally:
        # Force is test-harness cleanup only, never a product cancellation result.
        if peer.process is not None and peer.process.poll() is None:
            try:
                if peer.observation is not None and peer.observation.cell_id is not None:
                    await asyncio.wait_for(peer.interrupt(peer.observation.cell_id), 3)
                await asyncio.wait_for(peer.close(), 3)
            except (TimeoutError, WorkerError, OSError):
                peer.process.kill()
                await peer.join()
        peer.disconnect()
        assert peer.process is not None and peer.process.poll() is not None


@pytest.mark.parametrize(
    "body",
    [
        "import time; time.sleep(30)",
        "while True: pass",
        "import asyncio; await asyncio.sleep(30)",
        "@nh.function\ndef spin() -> None:\n    from nighthawk.harness.proposals import CodeProposal; from nighthawk.harness.dispatch import dispatch\n    dispatch(CodeProposal(source='def spin():\\n    while True: pass\\n', export_name_tuple=('spin',))).export_name_to_value['spin']()\nspin()",
    ],
)
async def test_worker_keeps_interface_responsive_during_synchronous_python(client: WorkerClient, body: str) -> None:
    response = await client.submit("print('entered', flush=True)\n" + body, mode="python")
    assert response.cell_id is not None
    await output_until(client, "entered")
    observation = client.observation
    assert observation is not None and observation.started_at is not None
    # Independent parent ticks and draft edits must happen before cancellation.
    draft = ""
    elapsed_list: list[float] = []
    for character in "日本語":
        await asyncio.sleep(0.03)
        draft += character
        elapsed_list.append(time.time() - observation.started_at)
    assert draft == "日本語"
    assert elapsed_list == sorted(set(elapsed_list))
    assert response.cell_id not in client._cell_id_to_completion
    await client.interrupt(response.cell_id)
    completion = await asyncio.wait_for(client.wait(response.cell_id), 5)
    assert completion.status == "cancelled"
    assert "42" in await execute(client, "6 * 7")


async def test_interrupt_targets_original_cell_and_preserves_namespace(client: WorkerClient) -> None:
    await execute(
        client,
        "import threading\n"
        "def make_reader():\n"
        "    lock = threading.Lock()\n"
        "    values = [17]\n"
        "    def read():\n"
        "        with lock:\n"
        "            return values[0]\n"
        "    return read\n"
        "reader = make_reader()\n"
        "identity = id(reader)",
    )
    first = await client.submit("import time\nprint('first started')\ntime.sleep(30)", mode="python")
    assert first.cell_id is not None
    await output_until(client, "first started")
    await client.interrupt(first.cell_id)
    assert (await client.wait(first.cell_id)).status == "cancelled"
    second = await client.submit(
        "print('second started')\nimport asyncio\nawait asyncio.sleep(0.2)\n(reader(), id(reader) == identity)", mode="python"
    )
    assert second.cell_id is not None
    await output_until(client, "second started")
    late = await client.interrupt(first.cell_id)
    assert late.status == "missing"
    completion = await client.wait(second.cell_id)
    assert completion.status == "success"
    assert any(record.text == "(17, True)" for record in completion.output_tuple)


@pytest.mark.parametrize("attempt", range(5))
async def test_output_flood_does_not_starve_control(client: WorkerClient, attempt: int) -> None:
    await execute(client, "import faulthandler, signal\nfaulthandler.register(signal.SIGUSR2)")
    response = await client.submit("print('flood started')\nwhile True:\n    print('x' * 6000)", mode="python")
    assert response.cell_id is not None
    async with asyncio.timeout(5):
        while client._last_output_sequence < 500:
            await asyncio.sleep(0.01)
    try:
        await asyncio.wait_for(client.interrupt(response.cell_id), 5)
    except TimeoutError:
        assert client.process is not None and client.process.pid is not None
        os.kill(client.process.pid, signal.SIGUSR2)
        await asyncio.sleep(0.2)
        pytest.fail("\n".join(record.text for record in client.read_presentation() if isinstance(record, NativeOutput)))
    completion = await asyncio.wait_for(client.wait(response.cell_id), 5)
    assert completion.status == "cancelled"
    assert any(isinstance(record, OutputGap) for record in client.read_presentation())
    assert any(record.semantic_kind == "status" and record.text == "cancelled" for record in completion.output_tuple)
    assert "42" in await execute(client, "42")


async def test_native_output_is_captured_and_never_mistaken_for_control(client: WorkerClient) -> None:
    await execute(client, "import os\nos.write(1, 'native 日本語\\n'.encode())\nos.write(2, b'native error\\n')\nprint('python stream')")
    records = await output_until(client, "native error")
    records.extend(await output_until(client, "python stream") if not any("python stream" in record.text for record in records) else ())
    native = [record for record in records if isinstance(record, NativeOutput)]
    assert any("native 日本語" in record.text and record.category == "stdout" for record in native)
    assert any("native error" in record.text and record.category == "stderr" for record in native)
    assert all(record.cell_id is None for record in native)
    assert any(isinstance(record, OutputRecord) and record.cell_id is not None and "python stream" in record.text for record in records)


async def test_background_task_output_does_not_acquire_the_foreground_cell_identity(client: WorkerClient) -> None:
    await execute(
        client,
        "import asyncio, sys\n"
        "release = asyncio.Event()\n"
        "async def background():\n"
        "    await release.wait()\n"
        "    print('background stdout')\n"
        "    print('background stderr', file=sys.stderr)\n"
        "task = asyncio.create_task(background())",
    )
    response = await client.submit(
        "release.set()\nawait task\nawait asyncio.create_task(background())\nprint('foreground stdout')\nprint('foreground stderr', file=sys.stderr)",
        mode="python",
    )
    assert response.cell_id is not None
    assert (await client.wait(response.cell_id)).status == "success"
    records = await output_until(client, "foreground stderr")
    background = [record for record in records if isinstance(record, OutputRecord) and record.text.startswith("background ")]
    foreground = [record for record in records if isinstance(record, OutputRecord) and record.text.startswith("foreground ")]
    assert len(background) == 4 and {record.category for record in background} == {"stdout", "stderr"}
    assert all(record.cell_id is None for record in background)
    assert len(foreground) == 2 and all(record.cell_id == response.cell_id for record in foreground)


async def test_blocked_output_transport_does_not_prevent_cessation(client: WorkerClient) -> None:
    # Stop the output receiver at its storage boundary. The execution owner must
    # still record and deliver completion over control after filling the pipe.
    with client._presentation_lock:
        response = await client.submit("for i in range(2000):\n    print('x' * 6000)\n'output boundary'", mode="python")
        assert response.cell_id is not None and response.observation is not None
        completion = await asyncio.wait_for(client.wait(response.cell_id), 5)
        assert completion.status == "success"
        assert any(record.text == "'output boundary'" for record in completion.output_tuple)
    assert "42" in await execute(client, "42")


async def test_submission_received_while_busy_is_not_queued(client: WorkerClient) -> None:
    first = await client.submit("import time\nprint('busy entered')\ntime.sleep(30)", mode="python")
    assert first.cell_id is not None
    await output_until(client, "busy entered")
    rejected_task = asyncio.create_task(client.submit("unexpected = True", mode="python"))
    # A request awaiting the owner is not yet acceptance. Interrupt wakes the owner.
    await asyncio.sleep(0.05)
    await client.interrupt(first.cell_id)
    rejected = await rejected_task
    assert rejected.status == "busy"
    await client.wait(first.cell_id)
    assert "False" in await execute(client, "'unexpected' in globals()")


async def test_startup_failure_is_reported_and_worker_reaped(tmp_path: Path) -> None:
    (tmp_path / "broken_factory.py").write_text(
        "print('startup diagnostic')\ndef build(*, execution_model=None, decision_model=None):\n    raise ValueError('expected startup failure')\n"
    )
    client = WorkerClient(directory=tmp_path, factory_reference="broken_factory:build")
    with pytest.raises(WorkerError, match="expected startup failure"):
        await asyncio.wait_for(client.start(), 15)
    assert client.process is not None and client.process.poll() is not None
    assert any(isinstance(record, (NativeOutput, OutputRecord)) for record in client.read_presentation())


async def test_pending_interrupt_during_startup_cannot_hit_first_cell(tmp_path: Path) -> None:
    (tmp_path / "slow_factory.py").write_text(
        "import time\n"
        "from nighthawk.session import SessionManager, create_session\n"
        "def build(*, execution_model=None, decision_model=None):\n"
        "    time.sleep(0.15)\n"
        "    temporary = create_session(execution_model=execution_model or 'openai-responses:gpt-6-luna', decision_model=decision_model)\n"
        "    manager = SessionManager(runner=temporary._runner)\n"
        "    temporary.close()\n"
        "    return manager\n"
    )
    client = WorkerClient(directory=tmp_path, factory_reference="slow_factory:build")
    try:
        startup = asyncio.create_task(client.start())
        await asyncio.sleep(0)
        pending = asyncio.create_task(client.interrupt("not-an-admitted-cell"))
        await asyncio.wait_for(startup, 15)
        assert (await pending).status == "missing"
        assert "42" in await execute(client, "42")
        assert (await asyncio.wait_for(client.close(), 5)).status == "closed"
    finally:
        if client.process is not None and client.process.poll() is None:
            client.process.kill()
            await client.join()
        client.disconnect()


async def test_worker_preserves_import_reload_future_flags_and_tracebacks(client: WorkerClient) -> None:
    module = client.directory / "worker_project.py"
    module.write_text("value = 17\n")
    assert "17" in await execute(client, "import worker_project\nworker_project.value")
    module.write_text("value = 123456\n")
    assert "123456" in await execute(
        client, "import importlib\nimportlib.invalidate_caches()\nimportlib.reload(worker_project)\nworker_project.value"
    )
    await execute(client, "from __future__ import annotations")
    assert "MissingType" in await execute(client, "def annotated(value: MissingType):\n    return value\nannotated.__annotations__")
    await execute(client, "def failing():\n    return 1 / 0\nalias = failing")
    response = await client.submit("def failing():\n    return 42\nalias()", mode="python")
    assert response.cell_id is not None
    completion = await client.wait(response.cell_id)
    assert completion.status == "failure"
    assert any("return 1 / 0" in record.text and "<nighthawk-session-" in record.text for record in completion.output_tuple)


async def test_worker_dispatches_typed_operations_without_synthesizing_python(client: WorkerClient) -> None:
    from nighthawk.session import (
        BindingMutationResult,
        InspectLocalRequest,
        LocalBindingObservation,
        LocalPage,
        ReadLocalsRequest,
        RenameLocalRequest,
    )

    await execute(client, "samples = [1, 2, 3]")
    result = await client.invoke(RenameLocalRequest("samples", "renamed"))
    assert isinstance(result, BindingMutationResult) and result.status == "applied"
    detail = await client.invoke(InspectLocalRequest("renamed"))
    assert isinstance(detail, LocalBindingObservation) and detail.preview == "[1, 2, 3]"
    page = await client.invoke(ReadLocalsRequest())
    assert isinstance(page, LocalPage)
    assert "True" in await execute(client, "'samples' not in globals() and renamed == [1, 2, 3]")


async def test_busy_palette_mutation_returns_before_synchronous_cell_unwinds(client: WorkerClient) -> None:
    from nighthawk.session import BindingMutationResult, RenameLocalRequest

    await execute(client, "samples = [1]")
    response = await client.submit("import time\nprint('operator busy')\ntime.sleep(30)", mode="python")
    assert response.cell_id is not None
    await output_until(client, "operator busy")
    result = await asyncio.wait_for(client.invoke(RenameLocalRequest("samples", "renamed")), 5)
    assert isinstance(result, BindingMutationResult) and result.status == "busy"
    assert response.cell_id not in client._cell_id_to_completion
    await client.interrupt(response.cell_id)
    await client.wait(response.cell_id)
    assert "False" in await execute(client, "'renamed' in globals()")


async def test_operator_cell_exit_closes_and_reaps_worker(client: WorkerClient) -> None:
    response = await client.submit("session.exit()", mode="python")
    assert response.cell_id is not None
    outcome = await asyncio.wait_for(client.wait(response.cell_id), 5)
    assert outcome.status == "success"
    async with asyncio.timeout(5):
        while not client.closed:
            await asyncio.sleep(0.01)
    await asyncio.wait_for(client.join(), 5)
    assert client.process is not None and client.process.poll() is not None
    assert client.process.returncode == 0


async def test_completion_transport_is_bounded_and_history_remains_retained(client: WorkerClient) -> None:
    from nighthawk.session import ReadHistoryRequest

    response = await client.submit("for i in range(100):\n    session._active_execution._record('answer', 'x' * 6000)", mode="python")
    assert response.cell_id is not None
    completion = await asyncio.wait_for(client.wait(response.cell_id), 10)
    assert completion.status == "success" and completion.output_incomplete
    assert len(completion.output_tuple) <= 20
    assert sum(len(record.text) for record in completion.output_tuple) <= 6000
    page = await client.invoke(ReadHistoryRequest())
    assert page.horizon >= 102  # type: ignore[union-attr]
    assert page.record_tuple[1].text == "x" * (6000 - len(page.record_tuple[0].text))  # type: ignore[union-attr]


async def test_disconnect_retains_background_owner_until_confirmed_cessation(tmp_path: Path) -> None:
    client = WorkerClient(directory=tmp_path)
    try:
        await client.start()
        await execute(
            client,
            """import asyncio, time
from pathlib import Path
from nighthawk.session import BackgroundObservation, InterruptionResult
async def owned_work():
    while not Path('release').exists():
        await asyncio.sleep(0.01)
    Path('ceased').write_text('joined')
task = asyncio.create_task(owned_work())
class Owner:
    def read(self):
        return BackgroundObservation('ignored', 'fixture', 'owned task', 'stopped' if task.done() else 'running', time.time(), True)
    def request_interrupt(self):
        Path('interrupted').write_text('unexpected')
        return InterruptionResult('requested', 'ignored')
owner = Owner()
session.background.register(owner)
""",
        )
        client.disconnect()
        assert client.process is not None and client.process.poll() is None
        (tmp_path / "release").write_text("release")
        await asyncio.wait_for(client.join(), 5)
        assert (tmp_path / "ceased").read_text() == "joined"
        assert not (tmp_path / "interrupted").exists()
        assert not client.closed
    finally:
        if client.process is not None and client.process.poll() is None:
            client.process.kill()
            await client.join()
        client.disconnect()


def test_late_busy_reply_cannot_regress_client_observation() -> None:
    from examples.repl.protocol import Observation, OperationResponse
    from nighthawk.session import BindingMutationResult

    client = WorkerClient(directory=Path.cwd())
    current = Observation(revision=3, observed_at=3, state="idle")
    client._dispatch(current)
    client._dispatch(
        OperationResponse(
            request_id="late",
            result=BindingMutationResult("busy", "old", "new", 1),
            observation=Observation(revision=1, observed_at=1, state="running", cell_id="finished"),
        )
    )
    assert client.observation is current


async def test_typed_palette_interrupt_wakes_synchronous_owner(client: WorkerClient) -> None:
    from nighthawk.session import InterruptExecutionRequest, InterruptionResult

    response = await client.submit("import time\nprint('typed interruption ready')\ntime.sleep(30)", mode="python")
    assert response.cell_id is not None
    await output_until(client, "typed interruption ready")
    result = await asyncio.wait_for(client.invoke(InterruptExecutionRequest()), 5)
    assert isinstance(result, InterruptionResult)
    assert result.target_id == response.cell_id and result.status in ("requested", "already_finished")
    assert (await client.wait(response.cell_id)).status == "cancelled"
    assert "42" in await execute(client, "42")


async def test_process_death_is_observed_without_control_eof(client: WorkerClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from examples.repl.client import WorkerExited

    # Suppress transport failure delivery to prove the process sentinel alone
    # resolves unfinished waits. No successful cell or Session outcome is invented.
    schedule = client._schedule
    from examples.repl.protocol import Failure

    def without_transport_failure(event):
        if not isinstance(event, Failure):
            schedule(event)

    monkeypatch.setattr(client, "_schedule", without_transport_failure)
    response = await client.submit("import asyncio, os\nawait asyncio.sleep(0.1)\nos._exit(7)", mode="python")
    assert response.cell_id is not None
    with pytest.raises(WorkerError, match="exited"):
        await asyncio.wait_for(client.wait(response.cell_id), 5)
    async with asyncio.timeout(5):
        while not isinstance(event := await client.events.get(), WorkerExited):
            pass
    assert event.exit_code == 7
    assert client.process is not None and client.process.poll() is not None
    assert not client.closed and response.cell_id not in client._cell_id_to_completion


async def test_delayed_typed_interrupt_cannot_target_a_later_cell(client: WorkerClient) -> None:
    from examples.repl.protocol import OperationRequest, OperationResponse
    from nighthawk.session import InterruptExecutionRequest, InterruptionResult

    first = await client.submit("42", mode="python")
    assert first.cell_id is not None
    await client.wait(first.cell_id)
    second = await client.submit("import asyncio\nprint('later cell entered')\nawait asyncio.sleep(30)", mode="python")
    assert second.cell_id is not None
    await output_until(client, "later cell entered")
    response = await client.request(OperationRequest(request_id="delayed", request=InterruptExecutionRequest(), cell_id=first.cell_id))
    assert isinstance(response, OperationResponse)
    assert response.result == InterruptionResult("already_finished", first.cell_id)
    assert second.cell_id not in client._cell_id_to_completion
    assert client.observation is not None and client.observation.execution is not None
    assert client.observation.execution.state == "running"
    await client.interrupt(second.cell_id)
    assert (await client.wait(second.cell_id)).status == "cancelled"


@pytest.mark.parametrize("route", ["keyboard", "palette", "python"])
async def test_interruption_cleanup_status_is_visible_for_every_route(client: WorkerClient, route: str) -> None:
    from examples.repl.state import ScreenState, status_text
    from nighthawk.session import InterruptExecutionRequest

    body = (
        "import asyncio\n"
        "print('cleanup fixture entered')\n"
        "try:\n" + ("    session.execution.interrupt()\n" if route == "python" else "") + "    await asyncio.sleep(30)\n"
        "except asyncio.CancelledError:\n"
        "    print('cleanup fixture waiting')\n"
        "    await asyncio.sleep(0.5)\n"
        "    assert asyncio.current_task().cancelling() == 1\n"
        "    print('cleanup fixture completed')\n"
    )
    response = await client.submit(body, mode="python")
    assert response.cell_id is not None
    await output_until(client, "cleanup fixture entered")
    if route == "keyboard":
        await client.interrupt(response.cell_id)
    elif route == "palette":
        await client.invoke(InterruptExecutionRequest())
    async with asyncio.timeout(5):
        while client.observation is None or client.observation.execution is None or client.observation.execution.state != "interruption_requested":
            await asyncio.sleep(0.01)
    assert response.cell_id not in client._cell_id_to_completion
    text = status_text(ScreenState(observation=client.observation), time.time(), no_animation=True)
    assert text.startswith("Interruption requested; cessation unconfirmed")
    await output_until(client, "cleanup fixture completed")
    assert (await client.wait(response.cell_id)).status == "cancelled"


async def test_silent_synchronous_cell_publishes_running_before_completion(client: WorkerClient) -> None:
    response = await client.submit("import time; time.sleep(30)", mode="python")
    assert response.cell_id is not None
    async with asyncio.timeout(5):
        while (
            client.observation is None
            or client.observation.cell_id != response.cell_id
            or client.observation.execution is None
            or client.observation.execution.state != "running"
        ):
            await asyncio.sleep(0.01)
    assert response.cell_id not in client._cell_id_to_completion
    await client.interrupt(response.cell_id)
    assert (await client.wait(response.cell_id)).status == "cancelled"


def test_signal_during_lock_entry_releases_the_lock_before_interrupting() -> None:
    from examples.repl.worker import _InterruptDeferral

    interruption = _InterruptDeferral()
    lock = threading.Lock()

    class InterruptedLock:
        def __enter__(self) -> None:
            lock.acquire()
            signal.raise_signal(signal.SIGUSR1)

        def __exit__(self, *arguments: object) -> None:
            lock.release()

    previous_handler = signal.signal(signal.SIGUSR1, lambda signum, frame: interruption.interrupt())
    try:
        with pytest.raises(KeyboardInterrupt), interruption.hold(), InterruptedLock():
            assert lock.locked()
        assert not lock.locked()
        with interruption.hold(), lock:
            assert lock.locked()
        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGUSR1)
    finally:
        signal.signal(signal.SIGUSR1, previous_handler)
