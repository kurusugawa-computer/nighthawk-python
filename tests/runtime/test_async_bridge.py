from __future__ import annotations

import asyncio
import contextvars
import faulthandler
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from nighthawk.runtime.async_bridge import _BridgeState, run_coroutine_synchronously


@pytest.mark.parametrize(
    "scenario",
    [
        "join",
        "repeated",
        "before_publication",
        "cleanup_error",
        "direct",
        "startup_error",
        "startup_interruption",
        "construction_error",
        "owned_shutdown",
        "result_after_interrupt",
        "started_then_interrupted",
        "bootstrap_interrupted",
        "async_generator_error",
        "async_generator_error_after_interruption",
    ],
)
def test_bridge_completion_in_independent_process(scenario: str) -> None:
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Task was destroyed" not in result.stderr
    assert "was never awaited" not in result.stderr
    assert "Exception in thread" not in result.stderr


def exercise(scenario: str) -> None:
    original = KeyboardInterrupt("first interruption")
    existing_cause = ValueError("existing cause")
    original.__cause__ = existing_cause
    started, cleanup, release, finished, returned = (threading.Event() for _ in range(5))
    context: contextvars.ContextVar[str] = contextvars.ContextVar("bridge-test", default="outside")
    cleanup_error = RuntimeError("cleanup failure")
    join_count = 0
    publication = threading.Event()
    original_start = threading.Thread.start

    def interrupted_start(self: threading.Thread) -> None:
        original_start(self)
        assert started.wait(5)
        raise original

    async def work() -> None:
        assert context.get() == "inside"
        started.set()
        try:
            while not release.is_set():
                await asyncio.sleep(0.001)
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
            if scenario in {"startup_error", "startup_interruption"}:
                startup_error = original if scenario == "startup_interruption" else cleanup_error
                with patch.object(threading.Thread, "start", side_effect=startup_error):
                    try:
                        run_coroutine_synchronously(construct)
                    except BaseException as exception:
                        assert exception is startup_error
                    else:
                        raise AssertionError("startup error lost")
                assert not started.is_set()
                return
            if scenario in {"started_then_interrupted", "bootstrap_interrupted"}:
                original_bootstrap = threading.Thread._bootstrap_inner  # pyright: ignore[reportAttributeAccessIssue]
                original_request_cancellation = _BridgeState.request_cancellation
                cancellation_requested = threading.Event()

                def request_cancellation(self: _BridgeState) -> None:
                    cancellation_requested.set()
                    original_request_cancellation(self)
                    publication.set()

                def delayed_bootstrap(self: threading.Thread) -> None:
                    assert publication.wait(5)
                    original_bootstrap(self)

                def interrupt_before_bootstrap(self: threading.Event, timeout: float | None = None) -> bool:
                    raise original

                def release_after_cancellation() -> None:
                    try:
                        for _ in range(5000):
                            if returned.is_set():
                                return
                            if cleanup.wait(0.001):
                                assert not returned.is_set()
                                return
                        raise AssertionError("startup cancellation was not delivered")
                    finally:
                        release.set()

                controller = threading.Thread(target=release_after_cancellation)
                controller.start()
                try:
                    if scenario == "started_then_interrupted":
                        with (
                            patch.object(threading.Thread, "start", interrupted_start),
                            patch.object(_BridgeState, "request_cancellation", request_cancellation),
                        ):
                            try:
                                run_coroutine_synchronously(construct)
                            finally:
                                returned.set()
                    else:
                        original_initialize = threading.Thread.__init__

                        def initialize(self: threading.Thread, *arguments, **keyword_name_to_value) -> None:
                            original_initialize(self, *arguments, **keyword_name_to_value)
                            started_event = self._started  # pyright: ignore[reportAttributeAccessIssue]
                            started_event.wait = lambda timeout=None: interrupt_before_bootstrap(started_event, timeout)

                        with (
                            patch.object(threading.Thread, "__init__", initialize),
                            patch.object(threading.Thread, "_bootstrap_inner", delayed_bootstrap),
                            patch.object(_BridgeState, "request_cancellation", request_cancellation),
                        ):
                            try:
                                run_coroutine_synchronously(construct)
                            finally:
                                returned.set()
                except KeyboardInterrupt as exception:
                    assert exception is original
                    assert cancellation_requested.is_set()
                    if started.is_set():
                        assert finished.is_set()
                else:
                    raise AssertionError("startup interruption lost")
                finally:
                    release.set()
                    publication.set()
                    controller.join()
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

    if scenario in {"owned_shutdown", "async_generator_error", "async_generator_error_after_interruption"}:
        asynchronous_generator_finished = threading.Event()
        executor_finished = threading.Event()
        retained: list[object] = []

        async def generator():
            try:
                yield 1
            finally:
                await asyncio.sleep(0.001)
                asynchronous_generator_finished.set()
                if scenario != "owned_shutdown":
                    raise cleanup_error

        def executor_work() -> None:
            release.wait(5)
            executor_finished.set()

        async def start_owned_work() -> None:
            instance = generator()
            retained.append(instance)
            assert await anext(instance) == 1
            asyncio.get_running_loop().run_in_executor(None, executor_work)
            release.set()
            if scenario == "async_generator_error_after_interruption":
                raise original

        if scenario == "async_generator_error_after_interruption":
            try:
                run_coroutine_synchronously(start_owned_work)
            except KeyboardInterrupt as exception:
                assert exception is original
                assert isinstance(exception.__cause__, BaseExceptionGroup)
                assert existing_cause in exception.__cause__.exceptions
                assert any(isinstance(cause, BaseExceptionGroup) and cleanup_error in cause.exceptions for cause in exception.__cause__.exceptions)
            else:
                raise AssertionError("interruption lost during async generator cleanup")
        elif scenario == "async_generator_error":
            try:
                run_coroutine_synchronously(start_owned_work)
            except BaseExceptionGroup as exception:
                assert cleanup_error in exception.exceptions
            else:
                raise AssertionError("async generator cleanup failure lost")
        else:
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
    faulthandler.dump_traceback_later(10)
    try:
        exercise(sys.argv[1])
    finally:
        faulthandler.cancel_dump_traceback_later()
