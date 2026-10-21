from __future__ import annotations

import asyncio
import inspect
import threading
from types import SimpleNamespace

import pytest
from tenacity import wait_none

from nighthawk.resilience import CircuitOpenError, CircuitState, circuit_breaker, fallback, retrying, timeout, vote
from nighthawk.resilience import _circuit_breaker as circuit_module


@pytest.mark.asyncio
async def test_async_circuit_composes_with_retry_timeout_vote_and_fallback() -> None:
    call_count = 0

    @circuit_breaker(fail_threshold=3, on=ValueError)
    async def unreliable(value: str) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("try again")
        return value

    recovered = retrying(attempts=2, on=ValueError, wait=wait_none())(unreliable)
    assert await timeout(seconds=5)(vote(count=3)(recovered))("ok") == "ok"
    assert call_count == 4
    assert inspect.iscoroutinefunction(unreliable)
    assert inspect.signature(unreliable) == inspect.signature(unreliable.__wrapped__)

    @circuit_breaker(fail_threshold=1, on=ValueError)
    async def unavailable(value: str) -> str:
        raise ValueError(value)

    async def replacement(value: str) -> str:
        return value

    composed = fallback(unavailable, replacement)
    assert await composed("first") == "first"
    assert unavailable.state == CircuitState.OPEN
    assert await composed("second") == "second"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_decorated_methods_bind_the_instance(asynchronous: bool) -> None:
    class Service:
        def __init__(self, prefix: str) -> None:
            self.prefix = prefix

        @circuit_breaker()
        def call(self, value: str) -> str:
            return self.prefix + value

        @circuit_breaker()
        async def call_async(self, value: str) -> str:
            return self.prefix + value

    service = Service("result:")
    if asynchronous:
        assert await service.call_async("ok") == "result:ok"
        assert inspect.iscoroutinefunction(service.call_async)
        assert service.call_async.state == CircuitState.CLOSED
    else:
        assert service.call("ok") == "result:ok"
        assert not inspect.iscoroutinefunction(service.call)
        assert service.call.state == CircuitState.CLOSED


def test_nested_circuits_keep_their_own_state_and_call_chain() -> None:
    @circuit_breaker(fail_threshold=1, on=ValueError)
    def unavailable() -> None:
        raise ValueError("unavailable")

    outer = circuit_breaker(fail_threshold=2, on=ValueError)(unavailable)
    with pytest.raises(ValueError):
        outer()
    assert unavailable.state == CircuitState.OPEN
    assert outer.state == CircuitState.CLOSED
    with pytest.raises(CircuitOpenError):
        outer()
    outer.reset()
    assert unavailable.state == CircuitState.OPEN


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("probe_exception_type", [None, ValueError, TypeError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_only_one_recovery_probe_runs_and_always_releases_its_slot(
    monkeypatch: pytest.MonkeyPatch,
    asynchronous: bool,
    probe_exception_type: type[BaseException] | None,
) -> None:
    current_time = [100.0]
    monkeypatch.setattr(circuit_module, "time", SimpleNamespace(monotonic=lambda: current_time[0]))
    started = threading.Event()
    finish = threading.Event()

    def operation(*, delayed: bool, exception_type: type[BaseException] | None) -> str:
        if delayed:
            started.set()
            assert finish.wait(5)
        if exception_type is not None:
            raise exception_type("unavailable")
        return "ok"

    async def async_operation(*, delayed: bool, exception_type: type[BaseException] | None) -> str:
        return await asyncio.to_thread(operation, delayed=delayed, exception_type=exception_type)

    protected = circuit_breaker(fail_threshold=1, reset_timeout=10, on=ValueError)(async_operation if asynchronous else operation)

    async def call(*, delayed: bool = False, exception_type: type[BaseException] | None = None):
        if asynchronous:
            result = protected(delayed=delayed, exception_type=exception_type)
            assert inspect.isawaitable(result)
            return await result
        return await asyncio.to_thread(protected, delayed=delayed, exception_type=exception_type)

    with pytest.raises(ValueError):
        await call(exception_type=ValueError)
    current_time[0] += 10
    assert protected.state == CircuitState.HALF_OPEN
    probe = asyncio.create_task(call(delayed=True, exception_type=probe_exception_type))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        with pytest.raises(CircuitOpenError, match="probe"):
            await call()
    finally:
        finish.set()
        if probe_exception_type is None:
            assert await probe == "ok"
        else:
            with pytest.raises(probe_exception_type):
                await probe

    if probe_exception_type is ValueError:
        assert protected.state == CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            await call()
        current_time[0] += 10
    elif probe_exception_type is not None:
        assert protected.state == CircuitState.HALF_OPEN
    assert await call() == "ok"
    assert protected.state == CircuitState.CLOSED


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("reset", [False, True])
@pytest.mark.parametrize("late_failure", [False, True])
@pytest.mark.asyncio
async def test_late_results_cannot_change_a_new_circuit_generation(
    monkeypatch: pytest.MonkeyPatch,
    asynchronous: bool,
    reset: bool,
    late_failure: bool,
) -> None:
    current_time = [100.0]
    monkeypatch.setattr(circuit_module, "time", SimpleNamespace(monotonic=lambda: current_time[0]))
    started = threading.Event()
    finish = threading.Event()

    def operation(*, delayed: bool, failing: bool) -> str:
        if delayed:
            started.set()
            assert finish.wait(5)
        if failing:
            raise ValueError("unavailable")
        return "ok"

    async def async_operation(*, delayed: bool, failing: bool) -> str:
        return await asyncio.to_thread(operation, delayed=delayed, failing=failing)

    protected = circuit_breaker(fail_threshold=1, reset_timeout=10, on=ValueError)(async_operation if asynchronous else operation)

    async def call(*, delayed: bool = False, failing: bool = False):
        if asynchronous:
            result = protected(delayed=delayed, failing=failing)
            assert inspect.isawaitable(result)
            return await result
        return await asyncio.to_thread(protected, delayed=delayed, failing=failing)

    previous = asyncio.create_task(call(delayed=True, failing=late_failure))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        with pytest.raises(ValueError):
            await call(failing=True)
        if reset:
            protected.reset()
        current_time[0] += 1
    finally:
        finish.set()
        if late_failure:
            with pytest.raises(ValueError):
                await previous
        else:
            assert await previous == "ok"

    assert protected.state == (CircuitState.CLOSED if reset else CircuitState.OPEN)
    if not reset:
        with pytest.raises(CircuitOpenError) as caught:
            await call()
        assert caught.value.time_remaining == 9
