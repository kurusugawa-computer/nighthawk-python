"""Milestone 0 whole-call, awaited finalization, and managed-bridge proofs."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

import nighthawk as nh
from nighthawk import function_call_scope
from nighthawk.agent import (
    CapabilityRejectedError,
    CodeProposal,
    DispatchRejectedError,
    Grant,
    RunContract,
    RunFailedError,
    RunHandle,
    RunRejectedError,
    ScaffoldComposition,
    current_grant,
    dispatch,
    scope,
)
from nighthawk.agent._host import current_host
from nighthawk.agent._natural_execution import _run_coroutine_synchronously
from nighthawk.agent.runtime import AsyncScaffoldComposition, bounded_resource_allocation
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.step_outcome import Pass
from nighthawk.runtime.step_request import StepCandidate
from nighthawk.testing import ScriptedExecutor

from .conftest import make_runner
from .milestone_zero_support import NaturalCallBoundary


@nh.function
def _record_body(observation_list: list[object]) -> int:
    host = current_host()
    observation_list.append(None if host is None else host.run_id)
    result: int = 0
    """execute
    Set <:result> to 7.
    """
    return result


_PRE_ACTIVATION_ALIAS = _record_body
_MODULE = SimpleNamespace(function=_record_body)


def _helper(observation_list: list[object]) -> int:
    return _PRE_ACTIVATION_ALIAS(observation_list)


@nh.function
def _nested(observation_list: list[object]) -> int:
    observation_list.append(current_grant())
    return _PRE_ACTIVATION_ALIAS(observation_list)


@pytest.mark.parametrize("function", [_record_body, _PRE_ACTIVATION_ALIAS, _MODULE.function, _helper, _nested])
def test_explicit_run_covers_sync_call_routes_and_sequential_allocations(function: Callable[[list[object]], int]) -> None:
    observation_list: list[object] = []
    handle_list: list[RunHandle] = []
    allocation_list: list[object] = []
    grant = Grant.of(selected="first")

    def allocate(identity: Any) -> Any:
        allocation = bounded_resource_allocation(identity=identity.run_id)
        allocation_list.append(allocation)
        return allocation

    for _ in range(2):
        handle = RunHandle()
        composition = ScaffoldComposition(RunContract(list, int), lambda value: function(observation_list), run_entry_grant=grant)
        assert (
            replace(
                make_runner(allocation_factory=allocate),
                step_executor=ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": 7})]),
            ).run(composition, observation_list, handle=handle)
            == 7
        )
        handle_list.append(handle)
        assert [entry.event.kind for entry in handle.ledger.entries].count("run_end") == 1
        assert handle.ledger.executor_step_count() == 1
    assert handle_list[0].run_id != handle_list[1].run_id
    assert allocation_list[0] is not allocation_list[1]
    assert [value for value in observation_list if isinstance(value, str)] == [handle.run_id for handle in handle_list]
    assert current_host() is None


def test_explicit_run_rejection_precedes_body_but_follows_argument_evaluation() -> None:
    observation_list: list[object] = []

    def argument() -> list[object]:
        observation_list.append("argument")
        return observation_list

    handle = RunHandle()
    with pytest.raises(RunRejectedError, match="executor_steps_unobservable"):
        replace(make_runner(accepts_aggregate_executor_accounting=False), step_executor=ScriptedExecutor(default_response=StepCandidate())).run(
            ScaffoldComposition(RunContract(list, int), _record_body), argument(), handle=handle
        )
    assert observation_list == ["argument"]
    assert handle.run_id is None


@pytest.mark.parametrize("function", [_PRE_ACTIVATION_ALIAS, _MODULE.function, _helper])
def test_core_call_scope_intercepts_existing_alias_module_and_helper_routes(
    monkeypatch: pytest.MonkeyPatch,
    function: Callable[[list[object]], int],
) -> None:
    def reject(*arguments: object, **keyword_arguments: object) -> object:
        raise AssertionError("replacement decorator must not be reached")

    monkeypatch.setattr(nh, "function", reject)
    observation_list: list[object] = []
    boundary = NaturalCallBoundary(
        replace(
            make_runner(),
            step_executor=ScriptedExecutor(
                responses=[StepCandidate(binding_name_to_value={"result": 7}), StepCandidate(binding_name_to_value={"result": 7})]
            ),
        ),
        {_record_body: int},
    )
    with function_call_scope(boundary):
        assert function(observation_list) == 7
        assert function(observation_list) == 7
    assert observation_list == [handle.run_id for handle in boundary.handle_list]
    assert len(boundary.handle_list) == 2 and observation_list[0] != observation_list[1]
    assert observation_list[0] is not None


def test_sync_entry_does_not_implicitly_await_a_coroutine_result() -> None:
    observation_list: list[str] = []

    async def body(value: int) -> int:
        observation_list.append("entered")
        await asyncio.sleep(0)
        return value + 1

    coroutine = body(6)
    handle = RunHandle()
    try:
        with pytest.raises(RunFailedError) as failure:
            replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate())).run(
                ScaffoldComposition(RunContract(int, int), lambda value: cast(int, coroutine)), 6, handle=handle
            )
        assert failure.value.terminal_kind == "validation_rejection"
        assert observation_list == []
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CREATED
        assert handle.ledger.entries[-1].event.kind == "run_end"
    finally:
        coroutine.close()


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_base_exception_finalizes_once_before_propagating(exception_type: type[BaseException]) -> None:
    def body(value: int) -> int:
        raise exception_type()

    handle = RunHandle()
    with pytest.raises(exception_type):
        replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate())).run(
            ScaffoldComposition(RunContract(int, int), body), 0, handle=handle
        )
    assert handle.run_id is not None
    assert [entry.event.kind for entry in handle.ledger.entries].count("run_end") == 1
    assert current_host() is None


@nh.function
async def _suspended(started: asyncio.Event, release: asyncio.Event, observation_list: list[object]) -> int:
    host = current_host()
    assert host is not None
    observation_list.append(host.run_id)
    started.set()
    await release.wait()
    assert current_host() is host
    return 7


@pytest.mark.parametrize("cancel", [False, True])
async def test_async_natural_admits_on_await_and_finalizes_after_completion(cancel: bool) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    observation_list: list[object] = []
    boundary = NaturalCallBoundary(replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate())), {_suspended: int})
    coroutine = _suspended(started, release, observation_list)
    assert not boundary.handle_list
    with function_call_scope(boundary):
        task = asyncio.create_task(coroutine)
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            handle = boundary.handle_list[0]
            assert observation_list == [handle.run_id]
            assert all(entry.event.kind != "run_end" for entry in handle.ledger.entries)
            assert current_host() is None
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                assert await task == 7
            assert [entry.event.kind for entry in handle.ledger.entries].count("run_end") == 1
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_awaited_specialization_accepts_completed_result_and_cleans_up_early_and_late_failure() -> None:
    for failure_position in (None, "before", "after"):
        handle = RunHandle()

        async def body(value: int, failure_position: str | None = failure_position) -> int:
            host = current_host()
            assert host is not None
            if failure_position == "before":
                raise ValueError("early")
            await asyncio.sleep(0)
            assert current_host() is host
            if failure_position == "after":
                raise ValueError("late")
            return value + 1

        call = replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate())).run(
            AsyncScaffoldComposition(RunContract(int, int), body), 6, handle=handle
        )
        assert handle.run_id is None
        if failure_position is None:
            assert await call == 7
        else:
            with pytest.raises(RunFailedError):
                await call
        assert [entry.event.kind for entry in handle.ledger.entries].count("run_end") == 1


@nh.function
async def _nested_await(observation_list: list[object]) -> None:
    observation_list.append(tuple(current_grant().references))
    await asyncio.sleep(0)
    observation_list.append(tuple(current_grant().references))


@nh.function
async def _outer_await(observation_list: list[object]) -> int:
    with scope(current_grant().without(["removed"])):
        await _nested_await(observation_list)
        result = _record_body(observation_list)
    observation_list.append(tuple(current_grant().references))
    return result


async def test_nested_await_and_sync_natural_call_retain_run_and_attenuation_then_sequential_calls_start_fresh() -> None:
    observation_list: list[object] = []
    boundary = NaturalCallBoundary(
        replace(
            make_runner(),
            step_executor=ScriptedExecutor(
                responses=[StepCandidate(binding_name_to_value={"result": 7}), StepCandidate(binding_name_to_value={"result": 7})]
            ),
        ),
        {_outer_await: int, _nested_await: type(None), _record_body: int},
        grant=Grant.of(selected=1, removed=2),
    )
    with function_call_scope(boundary):
        assert await _outer_await(observation_list) == 7
        assert await _outer_await(observation_list) == 7
    assert len(boundary.handle_list) == 2
    first, second = boundary.handle_list
    assert first.run_id != second.run_id
    assert observation_list == [
        ("selected",),
        ("selected",),
        first.run_id,
        ("selected", "removed"),
        ("selected",),
        ("selected",),
        second.run_id,
        ("selected", "removed"),
    ]
    assert first.ledger.executor_step_count() == second.ledger.executor_step_count() == 1


@nh.function
def _early_failure(observation_list: list[object]) -> int:
    host = current_host()
    observation_list.append(None if host is None else host.run_id)
    raise ValueError("before the first Natural block")


def test_call_admission_rejection_precedes_body_effects_and_early_body_failure_is_finalized() -> None:
    observation_list: list[object] = []
    boundary = NaturalCallBoundary(replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate())), {})
    with function_call_scope(boundary), pytest.raises(RunRejectedError):
        _early_failure(observation_list)
    assert observation_list == [] and boundary.handle_list == []
    admitted = NaturalCallBoundary(boundary.runner, {_early_failure: int})
    with function_call_scope(admitted), pytest.raises(RunFailedError) as failure:
        _early_failure(observation_list)
    assert isinstance(failure.value.__cause__, ValueError)
    assert observation_list == [admitted.handle_list[0].run_id]
    assert [entry.event.kind for entry in admitted.handle_list[0].ledger.entries].count("run_end") == 1


def test_call_scope_cannot_revive_an_expired_generated_natural_reference() -> None:
    source = "import nighthawk as nh\n@nh.function\ndef generated() -> int:\n    return 7\n"
    runner = replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate()))
    reference = runner.run(
        ScaffoldComposition(
            RunContract(type(None), object),
            lambda value: dispatch(CodeProposal(source=source, export_name_tuple=("generated",))).export_name_to_value["generated"],
        ),
        None,
    )
    assert callable(reference)
    boundary = NaturalCallBoundary(runner, {})
    with function_call_scope(boundary), pytest.raises(DispatchRejectedError) as failure:
        reference()
    assert failure.value.reason == "no_active_run"
    assert boundary.handle_list == []


def test_copied_ended_host_context_rejects_natural_body_before_effects() -> None:
    runner = replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate()))
    boundary = NaturalCallBoundary(runner, {_early_failure: int})
    with function_call_scope(boundary):
        copied = runner.run(ScaffoldComposition(RunContract(type(None), object), lambda value: contextvars.copy_context()), None)
    assert isinstance(copied, contextvars.Context)
    observation_list: list[object] = []
    with pytest.raises(CapabilityRejectedError) as failure:
        copied.run(_early_failure, observation_list)
    assert failure.value.reason_code == "run_ended"
    assert observation_list == []


async def test_top_level_await_uses_caller_loop_and_does_not_implicitly_await_expression_results() -> None:
    import ast

    loop = asyncio.get_running_loop()
    observation_list: list[object] = []

    async def work() -> int:
        observation_list.append(asyncio.get_running_loop())
        await asyncio.sleep(0)
        return 7

    binding_name_to_value: dict[str, Any] = {"work": work}
    code = compile("result = await work()", "<milestone-0-await>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    await eval(code, binding_name_to_value)
    assert binding_name_to_value["result"] == 7
    assert observation_list == [loop]
    expression = eval(compile("work()", "<milestone-0-expression>", "eval"), binding_name_to_value)
    try:
        assert inspect.getcoroutinestate(expression) == inspect.CORO_CREATED
        assert observation_list == [loop]
    finally:
        expression.close()


@pytest.mark.parametrize("bridge", [run_coroutine_synchronously, _run_coroutine_synchronously])
async def test_sync_bridges_copy_context_but_block_sibling_progress(bridge: Callable[..., object]) -> None:
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("milestone_zero_marker", default="outside")
    progressed: list[str] = []

    async def sibling() -> None:
        progressed.append("sibling")

    async def worker() -> str:
        await asyncio.sleep(0)
        assert progressed == []
        return marker.get()

    token = marker.set("cell")
    task = asyncio.create_task(sibling())
    try:
        assert bridge(worker) == "cell"
        assert progressed == []
        await task
        assert progressed == ["sibling"]
    finally:
        marker.reset(token)


@pytest.mark.parametrize("managed", [False, True])
async def test_interrupted_bridge_confirms_worker_cleanup_before_run_finalization(monkeypatch: pytest.MonkeyPatch, managed: bool) -> None:
    started = threading.Event()
    interrupted = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_join = threading.Thread.join
    handle = RunHandle()
    observation_list: list[bool] = []

    async def worker() -> int:
        started.set()
        try:
            assert release.wait(timeout=5)
            return 7
        finally:
            finished.set()

    class Executor:
        async def run_step_async(self, **keyword_arguments: Any) -> Any:
            value = await worker()
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": value})

    def interrupt_join(thread: threading.Thread, timeout: float | None = None) -> None:
        if thread.name == "nighthawk-sync-bridge":
            assert started.wait(timeout=5)
            interrupted.set()
            raise KeyboardInterrupt()
        original_join(thread, timeout)

    def observe_pending_and_release() -> None:
        try:
            observation_list.append(interrupted.wait(timeout=5))
            observation_list.append(not finished.is_set())
            if managed:
                observation_list.append(all(entry.event.kind != "run_end" for entry in handle.ledger.entries))
        finally:
            release.set()

    observer = threading.Thread(target=observe_pending_and_release)
    observer.start()
    monkeypatch.setattr(threading.Thread, "join", interrupt_join)
    try:
        with pytest.raises(KeyboardInterrupt):
            if managed:
                replace(make_runner(), step_executor=Executor()).run(ScaffoldComposition(RunContract(list, int), _record_body), [], handle=handle)
            else:
                run_coroutine_synchronously(worker)
        assert finished.is_set()
        assert all(observation_list)
        if managed:
            assert [entry.event.kind for entry in handle.ledger.entries].count("run_end") == 1
    finally:
        release.set()
        original_join(observer, 5)
        assert not observer.is_alive()
