from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace

import pytest

from nighthawk.session import (
    BackgroundObservation,
    CellInput,
    ExitResult,
    InterruptionResult,
    SessionClosedError,
)
from tests.support.session import session_manager


class ThreadOwner:
    def __init__(self) -> None:
        self.interrupted = threading.Event()
        self.release = threading.Event()
        self.joined = False
        self.read_failure = False
        self.interruption_count = 0
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def run(self) -> None:
        self.interrupted.wait()
        self.release.wait()

    def read(self) -> BackgroundObservation:
        if self.read_failure:
            raise RuntimeError("owner read failure")
        return BackgroundObservation(
            "adapter-may-not-choose-identity",
            "thread fixture",
            "Cooperative background thread",
            "stopped" if self.joined else "interruption_requested" if self.interrupted.is_set() else "running",
            time.time(),
            not self.joined,
        )

    def request_interrupt(self) -> InterruptionResult:
        self.interruption_count += 1
        self.interrupted.set()
        return InterruptionResult("requested", "adapter-may-not-choose-identity")

    def finish(self) -> None:
        self.interrupted.set()
        self.release.set()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()
        self.joined = True


def test_exit_refusal_leaves_session_unchanged() -> None:
    session = session_manager().create_session()
    work = ThreadOwner()
    try:
        session.binding_name_to_value["samples"] = [1, 2]
        work_id = session.background.register(work)
        assert work_id != work.read().work_id
        original = dict(session.binding_name_to_value)
        refused = session.exit()
        assert refused.status == "refused" and refused.blocker_tuple[0].work_id == work_id
        assert not session._closing and not session._disposed and not session._exit_requested
        assert session.binding_name_to_value == original
        assert work.interruption_count == 0 and work.thread.is_alive()
        assert session.submit(CellInput("> samples.append(3)")).wait().status == "success"
        assert session.background.interrupt(work_id) == InterruptionResult("requested", work_id)
        assert session.exit().status == "refused"
        assert not session._closing and work.thread.is_alive()
        work.finish()
        assert session.background.list()[0].state == "stopped"
        assert not session._closing and not session._disposed
        assert session.submit(CellInput("> samples")).wait().status == "success"
        assert session.exit() == ExitResult("closed")
        assert session.exit() == ExitResult("closed")
    finally:
        work.finish()
        session.close()


async def test_exit_from_operator_cell_finishes_without_self_wait() -> None:
    session = session_manager().create_session()
    observed: list[ExitResult] = []
    session.binding_name_to_value["observed"] = observed
    execution = session.submit(CellInput("> observed.append(session.exit())\nassert not session._disposed"))
    outcome = await asyncio.wait_for(execution.wait_async(), 5)
    assert outcome.status == "success"
    assert observed == [ExitResult("accepted")]
    assert session._disposed
    assert execution.outcome is outcome
    assert session.exit().status == "closed"


async def test_embedding_exit_never_exits_application() -> None:
    session = session_manager().create_session()
    assert session.exit().status == "closed"
    observed = []

    async def application_work() -> None:
        await asyncio.sleep(0)
        observed.append("application continues")

    await application_work()
    assert observed == ["application continues"]


async def test_external_exit_interrupts_only_after_successful_background_preflight() -> None:
    session = session_manager().create_session()
    entered, release = asyncio.Event(), asyncio.Event()
    session.binding_name_to_value.update(entered=entered, release=release)
    execution = session.submit(CellInput("> entered.set()\nawait release.wait()"))
    task = asyncio.create_task(execution.wait_async())
    await entered.wait()
    assert session.exit().status == "accepted"
    assert session.exit().status == "accepted"
    assert (await asyncio.wait_for(task, 5)).status == "cancelled"
    assert session._disposed
    with pytest.raises(SessionClosedError):
        session.submit(CellInput("> 42"))


def test_failed_background_observation_is_unknown_and_blocks_exit() -> None:
    session = session_manager().create_session()
    work = ThreadOwner()
    try:
        work_id = session.background.register(work)
        work.read_failure = True
        observed = session.background.list()[0]
        assert observed.state == "unknown" and observed.work_id == work_id
        assert session.exit().status == "refused"
        assert work.interruption_count == 0
        session.binding_name_to_value.update(work=work, stop=work.request_interrupt)
        grant, _ = session._selection()
        assert "work" not in grant and "stop" not in grant
    finally:
        work.read_failure = False
        work.finish()
        session.close()


def test_close_requests_background_cancellation_but_cannot_claim_disposal() -> None:
    session = session_manager().create_session()
    work = ThreadOwner()
    try:
        session.background.register(work)
        assert not session.close()
        assert work.interruption_count == 1 and not session._disposed
        with pytest.raises(SessionClosedError):
            session.background.register(work)
        work.finish()
        assert session.close()
    finally:
        work.finish()
        session.close()


def test_background_owner_identity_is_stable_across_reads() -> None:
    session = session_manager().create_session()
    work = ThreadOwner()
    try:
        work_id = session.background.register(work)
        original_read = work.read
        work.read = lambda: replace(original_read(), owner="changed", work_id="changed")
        assert session.background.register(work) == work_id
        observed = session.background.list()[0]
        assert observed.work_id == work_id and observed.owner == "thread fixture"
    finally:
        work.finish()
        session.close()


def test_registration_during_exit_preflight_requires_a_fresh_decision() -> None:
    session = session_manager().create_session()
    first, second = ThreadOwner(), ThreadOwner()
    try:
        session.background.register(first)
        first.finish()
        original_read = first.read
        registered = False

        def register_during_read() -> BackgroundObservation:
            nonlocal registered
            if not registered:
                registered = True
                session.background.register(second)
            return original_read()

        first.read = register_during_read
        result = session.exit()
        assert result.status == "refused" and "registration" in result.reason
        assert not session._closing and not session._exit_requested
        assert second.interruption_count == 0
        second.finish()
        assert session.exit().status == "closed"
    finally:
        first.finish()
        second.finish()
        session.close()
