"""Public foreground Sessions: ordinary Python, Natural runs, and caller-owned loops."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any

import pytest

from nighthawk.agent import PerimeterPolicy
from nighthawk.agent.runtime import Runner, bounded_resource_allocation
from nighthawk.agent.session import (
    CellInput,
    ConversationResult,
    SessionBusyError,
    SessionClosedError,
    SessionLookupError,
    SessionManager,
    SessionWaitContextError,
    UserFacingBlock,
    create_session,
)
from nighthawk.runtime.step_request import StepCandidate
from nighthawk.testing import ScriptedExecutor


def session_manager(*responses: Any) -> SessionManager:
    return SessionManager(
        runner=Runner(
            ScriptedExecutor(responses=list(responses)),
            PerimeterPolicy.NONE,
            lambda identity: bounded_resource_allocation(),
            accepts_aggregate_executor_accounting=True,
        )
    )


def test_python_only_construction_preserves_process_and_independent_namespaces() -> None:
    state = (os.getcwd(), tuple(sys.path), sys.modules["__main__"], sys.stdout, sys.stderr, signal.getsignal(signal.SIGINT))
    first, second = create_session(execution_model="openai-responses:gpt-6-luna"), create_session(execution_model="openai-responses:gpt-6-luna")
    try:
        execution = first.submit(CellInput("> samples = [1, 2, 3]"))
        assert execution.outcome is None
        with pytest.raises(SessionBusyError):
            first.submit(CellInput("> 2"))
        outcome = execution.wait()
        assert outcome.status == "success" and outcome.run_id_tuple == ()
        assert execution.wait() is outcome
        assert first.binding_name_to_value["samples"] == [1, 2, 3]
        assert "samples" not in second.binding_name_to_value
        assert first.close() and first.close()
        assert execution.wait() is outcome
        assert second.submit(CellInput("> 2 + 2")).wait().output_tuple[0].text == "4"
        assert state == (os.getcwd(), tuple(sys.path), sys.modules["__main__"], sys.stdout, sys.stderr, signal.getsignal(signal.SIGINT))
    finally:
        first.close()
        second.close()


def test_managed_natural_calls_annotations_aliases_sequential_runs_and_original_source() -> None:
    session = session_manager().create_session()
    try:
        source = "@nh.function\ndef add_one(value: int) -> int:\n    return value + offset\n"
        assert session.submit(CellInput(source, mode="python")).wait().run_id_tuple == ()
        execution = session.submit(CellInput("> offset = 1\nalias = add_one\na = alias(6)\noffset = 2\nb = add_one(a)\nb"))
        outcome = execution.wait()
        assert outcome.status == "success" and len(outcome.run_id_tuple) == 2
        assert session.binding_name_to_value["b"] == 9
        assert session.read_run_evidence(outcome.run_id_tuple[0]).evidence_tuple
        definition = session.submit(CellInput("> @nh.function\ndef bad():\n    reached.append(1)\n    return 3\nreached = []"))
        assert definition.wait().status == "success"
        failure = session.submit(CellInput("> bad()")).wait()
        assert failure.status == "failure" and failure.run_id_tuple == ()
        assert session.binding_name_to_value["reached"] == []
        assert "invalid_declaration" in failure.output_tuple[0].text
        with pytest.raises(SessionLookupError):
            session.read_run_evidence("unknown")
    finally:
        session.close()


async def test_async_cells_are_cooperative_and_wait_is_context_sensitive() -> None:
    session = session_manager().create_session()
    entered, release = asyncio.Event(), asyncio.Event()
    session.binding_name_to_value.update(entered=entered, release=release)
    execution = session.submit(CellInput("> entered.set()\nawait release.wait()\nanswer = 42"))
    with pytest.raises(SessionWaitContextError):
        execution.wait()
    task = asyncio.create_task(execution.wait_async())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert execution.outcome is None
        with pytest.raises(SessionBusyError):
            await execution.wait_async()
        with pytest.raises(SessionBusyError):
            session.submit(CellInput("> 1"))
        release.set()
        outcome = await task
        assert outcome.status == "success"
        assert session.binding_name_to_value["answer"] == 42
        assert execution.wait() is outcome
        assert await execution.wait_async() is outcome
    finally:
        release.set()
        await task
        session.close()


@pytest.mark.parametrize("external", [False, True])
async def test_cancellation_waits_for_unwind_and_keeps_prior_effects(external: bool) -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    entered = asyncio.Event()
    session.binding_name_to_value["entered"] = entered
    execution = session.submit(CellInput("> import asyncio\nbefore = 1\nentered.set()\nawait asyncio.Event().wait()\nafter = 2"))
    task = asyncio.create_task(execution.wait_async())
    await asyncio.wait_for(entered.wait(), 5)
    if external:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        execution.request_interrupt()
        assert (await task).status == "cancelled"
    assert execution.outcome is not None and execution.outcome.status == "cancelled"
    assert session.binding_name_to_value["before"] == 1
    assert "after" not in session.binding_name_to_value
    next_execution = session.submit(CellInput("> 3"))
    assert (await next_execution.wait_async()).status == "success"
    assert session.close()


def test_queued_cancel_close_and_callback_failure_do_not_replay_work() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    queued = session.submit(CellInput("> should_not_exist = 1"))
    queued.request_interrupt()
    assert queued.outcome is not None and queued.outcome.status == "cancelled"
    assert "should_not_exist" not in session.binding_name_to_value
    callbacks: list[str] = []

    def fail(output: Any) -> None:
        callbacks.append(output.kind)
        raise ValueError("presentation failure")

    execution = session.submit(CellInput("> calls = [1]\ncalls"))
    outcome = execution.wait(on_output=fail)
    assert outcome.status == "success"
    assert callbacks == ["result"]
    assert [output.kind for output in outcome.output_tuple] == ["result", "delivery_diagnostic", "status"]
    assert execution.wait(on_output=fail) is outcome
    assert callbacks == ["result"]
    queued = session.submit(CellInput("> impossible = 1"))
    assert session.close()
    assert queued.outcome is not None and queued.outcome.status == "cancelled"
    with pytest.raises(SessionClosedError):
        session.history.read()


def test_standard_async_conversation_retains_an_explicit_typed_result() -> None:
    session = session_manager(
        StepCandidate(
            binding_name_to_value={
                "result": ConversationResult(
                    (UserFacingBlock("answer", "The sum is 6."),),
                    {"total": 6},
                )
            }
        )
    ).create_session()
    try:
        session.submit(CellInput("> samples = [1, 2, 3]")).wait()
        execution = session.submit(CellInput("Explain samples and assign its sum to <:total>."))
        outcome = execution.wait()
        assert outcome.status == "success" and len(outcome.run_id_tuple) == 1
        assert session.binding_name_to_value["total"] == 6
        assert any(output.kind == "publication" and output.event_id for output in outcome.output_tuple)
    finally:
        session.close()


def test_bounded_failure_output_preserves_root_cause_and_terminal_error() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    source = (
        "try:\n"
        "    cause = ValueError('precise underlying cause')\n"
        "    cause.add_note('diagnostic detail ' * 1000)\n"
        "    raise cause\n"
        "except ValueError as cause:\n"
        "    raise RuntimeError('operation failed') from cause\n"
    )
    try:
        outcome = session.submit(CellInput(source, mode="python")).wait()
        assert outcome.status == "failure"
        error = next(output for output in outcome.output_tuple if output.kind == "error")
        assert "ValueError: precise underlying cause" in error.text
        assert "RuntimeError: operation failed" in error.text
        assert len(error.text) == 6000 and error.incomplete
        assert session.submit(CellInput("6 * 7", mode="python")).wait().status == "success"
    finally:
        session.close()
