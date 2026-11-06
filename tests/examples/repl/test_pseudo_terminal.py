from __future__ import annotations

import os
import re
import termios
import time
from pathlib import Path

import pyte
import pytest

from tests.examples.repl.terminal_support import TerminalProcess


def test_real_terminal_multiline_persistence_errors_and_interruptions(tmp_path: Path) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path))
    try:
        terminal.read_until("#>")
        terminal.send("discarded draft\x03")
        terminal.cell("samples = [1, 2, 3]")
        terminal.cell("samples")
        assert b"[1, 2, 3]" in terminal.output
        terminal.cell("def add_one(value: int) -> int:\n    return value + 1\nadd_one(6)")
        assert b"] 7" in terminal.output
        terminal.cell("raise ValueError('expected failure')", status="failure")
        terminal.cell("recovered = 42\nrecovered")
        terminal.cell("offset = 1\n@nh.function\ndef add_offset(value: int) -> int:\n    return value + offset")
        terminal.cell("first = add_offset(5)\noffset = 2\nsecond = add_offset(first)\nsecond")
        assert b"] 8" in terminal.output
        terminal.cell("@nh.function\nasync def awaited(value: int) -> int:\n    import asyncio\n    await asyncio.sleep(0)\n    return value + 1")
        terminal.cell("await awaited(second)")
        assert b"] 9" in terminal.output
        terminal.cell("def original_failure():\n    return 1 / 0\nalias = original_failure")
        terminal.cell("def original_failure():\n    return 'replacement'\nalias()", status="failure")
        assert b"return 1 / 0" in terminal.output and b"<nighthawk-session-" in terminal.output
        terminal.send("import time\nprint('SYNC_' + 'RUNNING', flush=True)\ntime.sleep(30)\nshould_not_exist = 1\x1b\r")
        terminal.read_until("SYNC_RUNNING")
        terminal.send("\x03")
        terminal.wait_new_status("cancelled")
        terminal.read_until("!>")
        terminal.cell("'should_not_exist' in globals()")
        assert b"] False" in terminal.output
        terminal.send("import asyncio\nprint('AWAIT_' + 'RUNNING', flush=True)\nawait asyncio.sleep(30)\x1b\r")
        terminal.read_until("AWAIT_RUNNING")
        terminal.send("\x03")
        terminal.wait_new_status("cancelled")
        terminal.read_until("!>")
        terminal.cell("6 * 7")
        terminal.finish()
    finally:
        terminal.close()


def test_project_imports_module_globals_and_reload_use_each_terminal_directory(tmp_path: Path) -> None:
    for i in range(2):
        directory = tmp_path / str(i)
        directory.mkdir()
        module_path = directory / "analysis.py"
        module_path.write_text(f"value = {i + 10}\ndef describe():\n    return value\n")
        terminal = TerminalProcess("--directory", str(directory))
        try:
            terminal.read_until("#>")
            terminal.cell("import analysis\nanalysis.describe()")
            assert f"] {i + 10}".encode() in terminal.output
            module_path.write_text("value = 12345\ndef describe():\n    return value\n")
            terminal.cell("import importlib\nimportlib.invalidate_caches()\nimportlib.reload(analysis)\nanalysis.describe()")
            assert b"] 12345" in terminal.output
            terminal.finish()
        finally:
            terminal.close()


@pytest.mark.parametrize("body", ["import time; time.sleep(30)", "while True: pass", "import asyncio; await asyncio.sleep(30)"])
def test_status_survives_resize_and_japanese_input_during_execution(tmp_path: Path, body: str) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path), "--no-animation")
    try:
        terminal.wait_screen(lambda screen: "#>" in screen and "Idle" in terminal.status_line)
        terminal.cell("samples = [1, 2, 3]")
        terminal.send("\x1b[200~print('LIVE_' + 'RUNNING')\n" + body + "\x1b[201~\x1b\r")
        terminal.read_until("LIVE_RUNNING")
        draft = "samples\n# 日本語の下書き e\u0301"
        terminal.send("\x1b[200~" + draft + "\x1b[201~")
        terminal.wait_screen(lambda screen: "日本語の下書き" in screen)
        terminal.wait_screen(lambda screen: "Running Python" in terminal.status_line)
        elapsed_values: set[str] = set()

        def elapsed_advances(screen: str) -> bool:
            status = terminal.status_line
            assert "Running Python" in status, screen
            elapsed = re.search(r"(\d+\.\ds)", status)
            if elapsed is not None:
                elapsed_values.add(elapsed[1])
            return len(elapsed_values) >= 3

        terminal.wait_screen(elapsed_advances)
        terminal.send("\x14")
        terminal.wait_screen(lambda screen: "Search:" in screen)
        assert "Running Python" in terminal.status_line
        terminal.send("samples")
        terminal.wait_screen(lambda screen: "samples = [1, 2, 3]" in screen)
        terminal.send("\x03")
        terminal.wait_screen(lambda screen: "!>" in screen and "日本語の下書き" in screen)
        assert "Running Python" in terminal.status_line
        terminal.resize(40, 16)
        terminal.wait_screen(lambda screen: "Running Python" in terminal.status_line and "日本語" in screen)
        terminal.send("\x03")
        terminal.wait_new_status("cancelled")
        terminal.wait_screen(lambda screen: "Idle (cancelled)" in terminal.status_line and "日本語" in screen)
        assert "日本語" in terminal.visible
        terminal.resize(100, 30)
        terminal.wait_screen(lambda screen: "Idle" in terminal.status_line)
        terminal.send("\x03")
        terminal.cell("samples")
        assert b"[1, 2, 3]" in terminal.output
        assert terminal.worker_process_id_set
        terminal.finish()
    finally:
        terminal.close()


def test_terminal_refuses_background_exit_until_cessation_and_fresh_request(tmp_path: Path) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path), "--no-animation")
    try:
        terminal.wait_screen(lambda screen: "#>" in screen and "Idle" in terminal.status_line)
        terminal.cell(
            "import asyncio, time\n"
            "from nighthawk.session import BackgroundObservation, InterruptionResult\n"
            "stop = asyncio.Event()\nrelease = asyncio.Event()\n"
            "async def background_body():\n    await stop.wait()\n    await release.wait()\n"
            "task = asyncio.create_task(background_body())\n"
            "class Owner:\n"
            "    def read(self):\n"
            "        return BackgroundObservation('', 'test task', 'Awaiting explicit release', 'stopped' if task.done() else 'running', time.time(), not task.done())\n"
            "    def request_interrupt(self):\n"
            "        stop.set()\n"
            "        return InterruptionResult('requested', '')\n"
            "owner = Owner()\nwork_id = session.background.register(owner)"
        )
        terminal.send("\x04")
        terminal.wait_screen(lambda screen: "Exit refused" in screen and "Awaiting explicit release" in screen)
        terminal.send("\x03")
        terminal.cell("assert not session._closing\nsession.background.interrupt(work_id)")
        terminal.send("\x04")
        terminal.wait_screen(lambda screen: "Exit refused" in screen)
        terminal.send("\x03")
        terminal.cell("release.set()\nawait task\nassert not session._closing")
        assert terminal.status is None
        terminal.cell("6 * 7")
        terminal.finish()
    finally:
        terminal.close()


def test_output_flood_keeps_visible_inspection_and_interruption_available(tmp_path: Path) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path), "--no-animation")
    try:
        terminal.wait_screen(lambda screen: "Idle" in terminal.status_line)
        terminal.cell("samples = [1, 2, 3]")
        terminal.send("\x1b[200~while True:\n    print('flood ' * 100)\x1b[201~\x1b\r")
        terminal.wait_screen(lambda screen: "flood flood" in screen and "Running Python" in terminal.status_line)
        terminal.send("\x14samples")
        terminal.wait_screen(lambda screen: "Search: samples" in screen and "samples = [1, 2, 3]" in screen)
        assert "Running Python" in terminal.status_line
        terminal.send("\x03\x03")
        # A late stream backlog may displace transcript rows; the fixed status
        # must still report the authoritative completed outcome.
        terminal.wait_screen(lambda screen: "Idle (cancelled)" in terminal.status_line)
        # Presentation records can be dropped under the flood. Observe the
        # next authoritative status transition instead of counting console records.
        terminal.send("\x1b[200~samples\x1b[201~\x1b\r")
        terminal.wait_screen(lambda screen: "Idle (success)" in terminal.status_line)
        terminal.finish()
    finally:
        terminal.close()


def test_palette_rename_preserves_draft_and_activates_only_after_parameters(tmp_path: Path) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path))
    try:
        terminal.wait_screen(lambda screen: "Idle" in terminal.status_line)
        terminal.cell("samples = [1, 2, 3]")
        terminal.send("len(renamed)\x10session.locals.rename\r")
        terminal.wait_screen(lambda screen: "name: Enter accepts this field" in screen)
        terminal.send("samples\rrenamed\r")
        terminal.wait_screen(lambda screen: "Enter activates the displayed operation" in screen)
        assert "status: applied" not in terminal.visible
        terminal.send("\r")
        terminal.wait_screen(lambda screen: "status: applied" in screen)
        terminal.send("\x03\x03")
        terminal.wait_screen(lambda screen: "!> len(renamed)" in screen)
        terminal.send("\x1b\r")
        terminal.wait_new_status("success")
        terminal.finish()
    finally:
        terminal.close()


def test_worker_crash_retains_diagnostics_and_restores_terminal_on_explicit_close(tmp_path: Path) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path), "--no-animation")
    try:
        terminal.wait_screen(lambda screen: "#>" in screen and "Idle" in terminal.status_line)
        terminal.cell("samples = [1, 2, 3]")
        terminal.send("import asyncio, os\nawait asyncio.sleep(0.1)\nos._exit(7)\x1b\r")
        terminal.wait_screen(lambda screen: "Worker exited (7)" in terminal.status_line)
        terminal.send("\x14samples")
        terminal.wait_screen(lambda screen: "History: received" in screen and "samples = [1, 2, 3]" in screen)
        terminal.send("\x1b[21~")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            process_id, status = os.waitpid(terminal.process_id, os.WNOHANG)
            if process_id:
                terminal.status = status
                assert os.waitstatus_to_exitcode(status) == 1
                attributes = termios.tcgetattr(terminal.descriptor)
                assert attributes[3] & termios.ICANON and attributes[3] & termios.ECHO
                for worker_id in terminal.worker_process_id_set:
                    with pytest.raises(ProcessLookupError):
                        os.kill(worker_id, 0)
                break
            terminal.receive()
        else:
            raise AssertionError(f"Diagnostic close did not restore the terminal:\n{terminal.visible}")
    finally:
        terminal.close()


@pytest.mark.parametrize("route", ["python", "palette"])
def test_explicit_exit_releases_terminal_and_reaps_execution_owner(tmp_path: Path, route: str) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path), "--no-animation")
    try:
        terminal.wait_screen(lambda screen: "Idle" in terminal.status_line)
        terminal.cell("samples = [1, 2, 3]")
        if route == "python":
            terminal.send("\x1b[200~session.exit()\x1b[201~\x1b\r")
        else:
            terminal.send("\x10session.exit\r")
            terminal.wait_screen(lambda screen: "Enter activates the displayed operation" in screen)
            terminal.send("\r")
        terminal.wait_exit()
        assert terminal.worker_process_id_set
        assert b"\x1b[?1049h" not in terminal.raw_output
        assert not any(f"\x1b[?{mode}h".encode() in terminal.raw_output for mode in (1000, 1002, 1003, 1006, 1015))
    finally:
        terminal.close()


def test_console_preserves_native_scrollback_and_bracketed_paste(tmp_path: Path) -> None:
    terminal = TerminalProcess("--directory", str(tmp_path), "--no-animation")
    history = pyte.HistoryScreen(100, 30, history=500)
    terminal.screen = history
    terminal.decoder = pyte.ByteStream(history)
    try:
        terminal.wait_screen(lambda screen: "Idle" in terminal.status_line)
        terminal.cell("for i in range(80):\n    print(f'copy_source_{i}: 日本語')")
        terminal.wait_screen(lambda screen: "copy_source_79: 日本語" in screen and "Idle" in terminal.status_line)
        retained = "\n".join("".join(cell.data for _, cell in sorted(line.items())) for line in history.history.top)
        assert "copy_source_0: 日本語" in retained
        assert "copy_source_79: 日本語" in terminal.visible
        assert b"\x1b[?1049h" not in terminal.raw_output
        assert not any(f"\x1b[?{mode}h".encode() in terminal.raw_output for mode in (1000, 1002, 1003, 1006, 1015))
        assert b"\x1b[?2004h" in terminal.raw_output
        pasted = "def copied():\n    return '日本語'\ncopied()"
        before = len(re.findall(rb"\[status\]", terminal.raw_output))
        terminal.send("\x1b[200~" + pasted + "\x1b[201~")
        terminal.wait_screen(lambda screen: "def copied():" in screen and "Idle" in terminal.status_line)
        assert len(re.findall(rb"\[status\]", terminal.raw_output)) == before
        terminal.send("\x1b\r")
        terminal.wait_new_status("success")
        terminal.wait_screen(lambda screen: "Idle (success)" in terminal.status_line)
        assert "日本語" in terminal.visible
        terminal.finish()
    finally:
        terminal.close()
