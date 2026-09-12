from __future__ import annotations

import asyncio
import contextvars
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from nighthawk.runtime.async_bridge import run_coroutine_synchronously


@pytest.mark.parametrize(
    "scenario",
    [
        "join",
        "repeated",
        "before_publication",
        "cleanup_error",
        "direct",
        "startup_error",
        "construction_error",
        "owned_shutdown",
        "result_after_interrupt",
    ],
)
def test_bridge_completion_in_independent_process(scenario: str) -> None:
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Task was destroyed" not in result.stderr
    assert "was never awaited" not in result.stderr


def exercise(scenario: str) -> None:
    original = KeyboardInterrupt("first interruption")
    existing_cause = ValueError("existing cause")
    original.__cause__ = existing_cause
    started, cleanup, release, finished, returned = (threading.Event() for _ in range(5))
    context: contextvars.ContextVar[str] = contextvars.ContextVar("bridge-test", default="outside")
    cleanup_error = RuntimeError("cleanup failure")
    join_count = 0
    publication = threading.Event()

    async def work() -> None:
        assert context.get() == "inside"
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if scenario != "result_after_interrupt":
                raise
        finally:
            cleanup.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            finished.set()
            if scenario == "cleanup_error":
                raise cleanup_error

    def construct():
        if scenario == "before_publication":
            assert publication.wait(5)
        if scenario == "construction_error":
            raise cleanup_error
        return work()

    def interrupted_join(self: threading.Thread, timeout: float | None = None) -> None:
        nonlocal join_count
        join_count += 1
        if join_count == 1:
            if scenario != "before_publication":
                assert started.wait(5)
            publication.set()
            raise original
        if join_count == 2 and scenario == "repeated":
            assert cleanup.wait(5)
            raise KeyboardInterrupt("second interruption")
        # Simulate CPython join status becoming misleading after interruption.
        if scenario == "before_publication":
            release.set()
        elif cleanup.wait(0.01):
            assert not returned.is_set()
            release.set()

    async def invoke() -> None:
        token = context.set("inside")
        try:
            if scenario == "construction_error":
                try:
                    run_coroutine_synchronously(construct)
                except RuntimeError as exception:
                    assert exception is cleanup_error
                else:
                    raise AssertionError("missing construction error")
                return
            if scenario == "startup_error":
                with patch.object(threading.Thread, "start", side_effect=cleanup_error):
                    try:
                        run_coroutine_synchronously(construct)
                    except RuntimeError as exception:
                        assert exception is cleanup_error
                assert not started.is_set()
                return
            with patch.object(threading.Thread, "join", interrupted_join):
                try:
                    run_coroutine_synchronously(construct)
                except KeyboardInterrupt as exception:
                    assert exception is original
                    cause = exception.__cause__
                    assert cause is existing_cause or isinstance(cause, BaseExceptionGroup)
                    if scenario == "cleanup_error":
                        assert isinstance(cause, BaseExceptionGroup)
                        assert cleanup_error in cause.exceptions
                else:
                    raise AssertionError("interruption lost")
                returned.set()
                if scenario != "before_publication":
                    assert finished.is_set()
        finally:
            release.set()
            context.reset(token)

    if scenario == "owned_shutdown":
        asynchronous_generator_finished = threading.Event()
        executor_finished = threading.Event()
        retained: list[object] = []

        async def generator():
            try:
                yield 1
            finally:
                await asyncio.sleep(0.001)
                asynchronous_generator_finished.set()

        def executor_work() -> None:
            release.wait(5)
            executor_finished.set()

        async def start_owned_work() -> None:
            instance = generator()
            retained.append(instance)
            assert await anext(instance) == 1
            asyncio.get_running_loop().run_in_executor(None, executor_work)
            release.set()

        run_coroutine_synchronously(start_owned_work)
        assert asynchronous_generator_finished.is_set()
        assert executor_finished.is_set()
    elif scenario == "direct":

        async def direct() -> None:
            loop = asyncio.get_running_loop()

            def interrupt() -> None:
                raise original

            loop.call_soon(interrupt)
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.001)
                finished.set()

        try:
            run_coroutine_synchronously(direct)
        except KeyboardInterrupt as exception:
            assert exception is original
        else:
            raise AssertionError("direct interruption lost")
        assert finished.is_set()
    else:
        asyncio.run(invoke())
    assert context.get() == "outside"


if __name__ == "__main__":
    exercise(sys.argv[1])
