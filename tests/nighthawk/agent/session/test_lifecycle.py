"""Retained cleanup ownership, immutable output, and actual branch cessation."""

from __future__ import annotations

import asyncio
import contextvars
import gc
import sys
import threading
import weakref
from dataclasses import replace
from typing import Any, cast

import pytest

from nighthawk.agent import (
    AsyncGeneratedCallableReference,
    CodeProposal,
    DependencyDeclaration,
    DispatchRejectedError,
    GeneratedCallableReference,
    RunEndEvent,
    RunTerminationError,
    dispatch,
)
from nighthawk.agent.session import (
    CellInput,
    ConversationInput,
    ConversationResult,
    SessionBusyError,
    SessionClosedError,
    SessionManager,
    SessionWaitContextError,
    create_session,
)

from ..conftest import ConfirmedConfinement
from .test_conversation import configured


@pytest.mark.parametrize(
    "continuation,request_close",
    [(continuation, request_close) for continuation in ("propagate", "catch", "call_again") for request_close in (False, True)] + [("join", False)],
)
def test_wait_reattaches_only_to_cleanup_after_unconfirmed_branch_cessation(request_close: bool, continuation: str) -> None:
    entered, release = threading.Event(), threading.Event()
    threads: list[threading.Thread] = []
    calls: list[int] = []

    async def body(value: ConversationInput) -> ConversationResult:
        calls.append(1)
        result = dispatch(
            CodeProposal(
                source="def block() -> int:\n    entered.set()\n    release.wait()\n    return 7\n",
                export_name_tuple=("block",),
                dependency_tuple=(
                    DependencyDeclaration(name="entered", kind="reference", declared_type=threading.Event),
                    DependencyDeclaration(name="release", kind="reference", declared_type=threading.Event),
                ),
            )
        )
        function = cast(GeneratedCallableReference, result.export_name_to_value["block"])
        context = contextvars.copy_context()
        thread = threading.Thread(target=lambda: context.run(function))
        threads.append(thread)
        thread.start()
        assert entered.wait(timeout=5)
        return ConversationResult(())

    manager = configured(body)
    session = SessionManager(
        runner=replace(manager._runner, perimeter_confinement=ConfirmedConfinement()),
        conversation_composition_builder=manager._builder,
    ).create_session()
    session.binding_name_to_value.update(entered=entered, release=release)
    if continuation != "propagate":
        session.binding_name_to_value.update(start_branch=body, RunTerminationError=RunTerminationError, _branch_threads=threads)
        followup = "await start()\n" if continuation == "call_again" else ""
        if continuation == "join":
            followup = "release.set()\nfor thread in _branch_threads:\n    thread.join(timeout=5)\n"
        cell = CellInput(
            "@nh.function\n"
            "async def start() -> None:\n"
            "    await start_branch(None)\n"
            "try:\n"
            "    await start()\n"
            "except RunTerminationError:\n"
            "    pass\n" + followup,
            mode="python",
        )
    else:
        cell = CellInput("Start the observed branch.")
    execution = session.submit(cell)
    try:
        if continuation == "join":
            outcome = execution.wait()
            assert outcome.status == "success" and len(outcome.run_id_tuple) == 1
            ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
            assert len(ledger.select(RunEndEvent)) == 1
            assert calls == [1]
            assert execution.wait() is outcome
            assert session.close()
            return
        with pytest.raises(RunTerminationError):
            execution.wait()
        assert execution.outcome is None and len(execution.run_id_tuple) == 1
        ledger = session._run_id_to_ledger[execution.run_id_tuple[0]]
        assert not ledger.select(RunEndEvent)
        blockers = session.background.list()
        assert len(blockers) == 1
        assert blockers[0].run_id == execution.run_id_tuple[0]
        assert blockers[0].cell_id == execution.cell_id
        assert session.exit().status == "refused"
        assert not session._closing and not execution._interrupted
        assert session.read_run_evidence(execution.run_id_tuple[0]).evidence_tuple
        with pytest.raises(SessionBusyError):
            session.submit(CellInput("> never = 1"))
        with pytest.raises(RunTerminationError):
            execution.wait()
        if request_close:
            assert not session.close()
            with pytest.raises(SessionClosedError):
                session.submit(CellInput("> never = 2"))
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
    outcome = execution.wait()
    assert outcome.status == ("cancelled" if request_close else "failure")
    assert calls == [1]
    assert len(ledger.select(RunEndEvent)) == 1
    assert execution.wait() is outcome
    assert session.close()
    assert execution.wait() is outcome


def test_disposal_preserves_escaped_python_objects_and_removes_only_owned_module() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    module_names = [name for name in sys.modules if session.session_id in name]
    assert len(module_names) == 1
    outcome = session.submit(CellInput("> value = 6\ndef escaped() -> int:\n    return value + 1\n__name__ = 'changed_by_operator'")).wait()
    assert outcome.status == "success"
    escaped = cast(Any, session.binding_name_to_value["escaped"])
    assert session.close()
    assert escaped() == 7
    assert module_names[0] not in sys.modules


def test_unretained_expression_result_and_failed_cell_frames_are_collectable() -> None:
    references: list[weakref.ReferenceType[object]] = []

    class Value:
        def __init__(self) -> None:
            references.append(weakref.ref(self))

    session = create_session(execution_model="openai-responses:gpt-6-luna")
    session.binding_name_to_value["Value"] = Value
    try:
        execution = session.submit(CellInput("> Value()"))
        assert execution.wait().status == "success"
        failed = session.submit(CellInput("> def fail():\n    transient = Value()\n    raise ValueError('failure')\nfail()"))
        assert failed.wait().status == "failure"
        gc.collect()
        assert len(references) == 2
        assert all(reference() is None for reference in references)
    finally:
        session.close()


def test_pending_async_branch_requires_its_owning_loop_before_cleanup_reattachment() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    tasks: list[asyncio.Task[object]] = []

    async def body(value: ConversationInput) -> ConversationResult:
        result = dispatch(
            CodeProposal(
                source="async def block() -> int:\n    entered.set()\n    await release.wait()\n    return 7\n",
                export_name_tuple=("block",),
                dependency_tuple=(
                    DependencyDeclaration(name="entered", kind="reference", declared_type=asyncio.Event),
                    DependencyDeclaration(name="release", kind="reference", declared_type=asyncio.Event),
                ),
            )
        )
        function = cast(AsyncGeneratedCallableReference, result.export_name_to_value["block"])
        tasks.append(asyncio.create_task(function()))
        await entered.wait()
        return ConversationResult(())

    manager = configured(body)
    session = SessionManager(
        runner=replace(manager._runner, perimeter_confinement=ConfirmedConfinement()),
        conversation_composition_builder=manager._builder,
    ).create_session()
    session.binding_name_to_value.update(entered=entered, release=release)
    execution = session.submit(CellInput("Start an observed async branch."))
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(RunTerminationError):
            loop.run_until_complete(execution.wait_async())
        with pytest.raises(SessionWaitContextError):
            execution.wait()
        assert execution.outcome is None and not tasks[0].done()

        async def finish() -> None:
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert isinstance(results[0], DispatchRejectedError)
            assert results[0].reason == "run_ended"
            assert (await execution.wait_async()).status == "failure"

        loop.run_until_complete(finish())
        assert session.close()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        loop.close()
        session.close()
