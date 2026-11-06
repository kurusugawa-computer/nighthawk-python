from __future__ import annotations

import errno
import fcntl
import os
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from collections.abc import Callable
from contextlib import suppress

import pyte
import pytest

pty = pytest.importorskip("pty", reason="A real controlling terminal requires POSIX")


class TerminalProcess:
    def __init__(self, *arguments: str) -> None:
        self.process_id, self.descriptor = pty.fork()
        if self.process_id == 0:
            os.environ["TERM"] = "xterm-256color"
            os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"
            os.execv(sys.executable, [sys.executable, "-m", "examples.repl", *arguments])
        self.screen = pyte.Screen(100, 30)
        self.decoder = pyte.ByteStream(self.screen)
        self.raw_output = b""
        self.output = b""
        self.position = 0
        self.status: int | None = None
        self.worker_process_id_set: set[int] = set()
        self.seen_status_count = 0
        self.resize(100, 30)

    @property
    def visible(self) -> str:
        return "\n".join(self.screen.display)

    @property
    def status_line(self) -> str:
        return next((line for line in reversed(self.screen.display) if line.strip()), "")

    def resize(self, columns: int, lines: int) -> None:
        fcntl.ioctl(self.descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", lines, columns, 0, 0))
        self.screen.resize(lines, columns)
        os.kill(self.process_id, signal.SIGWINCH)

    def send(self, text: str) -> None:
        os.write(self.descriptor, text.encode())

    def receive(self, timeout: float = 0.1) -> None:
        ready, _, _ = select.select([self.descriptor], [], [], timeout)
        if not ready:
            return
        try:
            chunk = os.read(self.descriptor, 65536)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            chunk = b""
        if not chunk:
            return
        self.raw_output += chunk
        self.decoder.feed(chunk)
        self.output += self.visible.encode() + b"\n"

    def wait_screen(self, predicate: Callable[[str], bool], timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while not predicate(self.visible):
            if time.monotonic() >= deadline:
                raise AssertionError(f"Terminal screen condition was not met:\n{self.visible}")
            self.receive()
        self.track_worker()

    def read_until(self, marker: str, *, timeout: float = 15) -> None:
        target = marker.encode()
        deadline = time.monotonic() + timeout
        while True:
            found = self.output.find(target, self.position)
            if found >= 0:
                self.position = found + len(target)
                self.track_worker()
                return
            if time.monotonic() >= deadline:
                raise AssertionError(f"Terminal did not show {marker!r}:\n{self.visible}")
            self.receive()

    def track_worker(self) -> None:
        listing = subprocess.check_output(["ps", "-ww", "-axo", "pid=,ppid=,args="], text=True)
        for line in listing.splitlines():
            fields = line.split(None, 2)
            if len(fields) == 3 and fields[1] == str(self.process_id) and "examples.repl.worker" in fields[2]:
                self.worker_process_id_set.add(int(fields[0]))

    def wait_new_status(self, status: str, *, timeout: float = 15) -> None:
        def completed(screen: str) -> bool:
            records = re.findall(rb"\[status(?: cell \d+)?\] (success|failure|cancelled)", self.raw_output)
            if len(records) <= self.seen_status_count:
                return False
            self.seen_status_count = len(records)
            if records[-1].decode() != status:
                output = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", self.raw_output).decode(errors="replace").replace("\r", "")
                marker = "Failure chain (cause first):"
                summary = output.rsplit(marker, 1)[-1].split("\n\n", 1)[0] if marker in output else "No exception summary was emitted."
                raise AssertionError(f"Terminal completed with {records[-1].decode()!r}; expected {status!r}:\n{summary[:5000]}\n{screen}")
            return True

        self.wait_screen(completed, timeout=timeout)

    def cell(self, source: str, *, status: str = "success") -> None:
        self.send("\x7f! ")
        self.send("\x1b[200~" + source + "\x1b[201~")
        self.send("\x1b\r")
        self.wait_new_status(status)
        self.wait_screen(lambda screen: f"Idle ({status})" in self.status_line)

    def finish(self) -> None:
        self.track_worker()
        self.send("\x04")
        self.wait_exit()

    def wait_exit(self) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            process_id, status = os.waitpid(self.process_id, os.WNOHANG)
            if process_id:
                self.status = status
                assert os.waitstatus_to_exitcode(status) == 0, self.visible
                attributes = termios.tcgetattr(self.descriptor)
                assert attributes[3] & termios.ICANON and attributes[3] & termios.ECHO
                for worker_id in self.worker_process_id_set:
                    with pytest.raises(ProcessLookupError):
                        os.kill(worker_id, 0)
                return
            self.receive()
        raise AssertionError(f"Terminal did not exit after confirmed shutdown:\n{self.visible}")

    def close(self) -> None:
        if self.status is None:
            self.track_worker()
            for worker_id in self.worker_process_id_set:
                with suppress(ProcessLookupError):
                    os.kill(worker_id, signal.SIGKILL)
            os.kill(self.process_id, signal.SIGKILL)
            os.waitpid(self.process_id, 0)
        os.close(self.descriptor)
