"""Milestone 0 cooperative core and host contexts through a shared runner.

All tasks belong to the caller and all started tasks are joined in cleanup.
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
from dataclasses import replace
from typing import Any

import pytest
from pydantic_ai.usage import RunUsage

import nighthawk as nh
from nighthawk import function_call_scope
from nighthawk.agent import Grant, NaturalExecutionOutcomeEvent, RunFailedError, RunRejectedError, current_executor_step_admission, current_grant
from nighthawk.agent._host import current_host
from nighthawk.agent.runtime import bounded_resource_allocation
from nighthawk.runtime.step_outcome import Pass
from nighthawk.runtime.step_request import StepCandidate, StepRequest
from nighthawk.testing import ScriptedExecutor

from ..conftest import make_runner
from ..milestone_zero_support import NaturalCallBoundary
from .test_source_adoption import _DefinitionExperiment

_CELL_ID: contextvars.ContextVar[str] = contextvars.ContextVar("milestone_zero_cell_id", default="application")


@nh.function
async def _cooperative(value: int) -> int:
    result: int = 0
    """execute
    Set <:result> from <value>.
    """
    return result


class _SharedExecutor:
    def __init__(self) -> None:
        self.cell_id_to_started = {name: asyncio.Event() for name in ("first", "second")}
        self.cell_id_to_release = {name: asyncio.Event() for name in ("first", "second")}
        self.cell_id_to_selection: dict[str, dict[str, object]] = {}
        self.finished_cell_id_list: list[str] = []

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        cell_id = _CELL_ID.get()
        self.cell_id_to_selection[cell_id] = dict(nh.get_step_context().environment.task_name_to_value)
        self.cell_id_to_started[cell_id].set()
        try:
            await self.cell_id_to_release[cell_id].wait()
            meter = nh.get_scope().usage_meter
            assert meter is not None
            meter.record(RunUsage(requests=1, input_tokens=7, output_tokens=1))
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 7})
        finally:
            self.finished_cell_id_list.append(cell_id)


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_core_contexts_are_independent_and_application_calls_remain_unaffected(cancel_first: bool) -> None:
    executor = _SharedExecutor()
    cell_id_to_usage: dict[str, Any] = {}
    cell_id_to_binding: dict[str, dict[str, int]] = {}

    async def cell(cell_id: str) -> int:
        token = _CELL_ID.set(cell_id)
        try:
            with nh.run(executor, run_id=cell_id), nh.scope(task_references={cell_id: cell_id}):
                meter = nh.get_scope().usage_meter
                assert meter is not None
                try:
                    result = await _cooperative(6)
                    cell_id_to_binding[cell_id] = {"result": result}
                    return result
                finally:
                    cell_id_to_usage[cell_id] = meter.snapshot()
        finally:
            _CELL_ID.reset(token)

    first = asyncio.create_task(cell("first"))
    second = asyncio.create_task(cell("second"))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in executor.cell_id_to_started.values())), timeout=5)
        assert not first.done() and not second.done()
        assert executor.cell_id_to_selection == {"first": {"first": "first"}, "second": {"second": "second"}}
        assert _CELL_ID.get() == "application"
        assert current_host() is None
        with nh.run(ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": 99})])):
            assert await _cooperative(0) == 99
        if cancel_first:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not second.done()
        else:
            executor.cell_id_to_release["first"].set()
            assert await first == 7
        executor.cell_id_to_release["second"].set()
        assert await second == 7
        assert cell_id_to_usage["first"].requests == (0 if cancel_first else 1)
        assert cell_id_to_usage["second"].requests == 1
        assert cell_id_to_binding["second"] == {"result": 7}
        assert ("first" in cell_id_to_binding) is not cancel_first
        assert sorted(executor.finished_cell_id_list) == ["first", "second"]
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.parametrize("first_outcome", ["success", "cancel", "exhaust"])
async def test_observed_cells_share_runner_but_not_approval_allocation_usage_or_cancellation(first_outcome: str) -> None:
    cell_id_to_started = {name: asyncio.Event() for name in ("first", "second")}
    cell_id_to_release = {name: asyncio.Event() for name in ("first", "second")}
    request_cell_id_list: list[str] = []
    allocation_list: list[object] = []

    class Executor:
        admits_executor_steps = True

        async def run_step_async(self, **keyword_arguments: Any) -> Any:
            cell_id = str(current_grant().references["cell_id"])
            admission = current_executor_step_admission()
            admission.admit()
            request_cell_id_list.append(cell_id)
            cell_id_to_started[cell_id].set()
            await cell_id_to_release[cell_id].wait()
            admission.observe(RunUsage(requests=1, input_tokens=11 if cell_id == "first" else 17))
            if cell_id == "first" and first_outcome == "exhaust":
                admission.admit()
                raise AssertionError("an exhausted request reached the provider")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 7})

    def allocate(identity: Any) -> Any:
        allocation = bounded_resource_allocation(identity=identity.run_id, maximum_executor_step_count=1)
        allocation_list.append(allocation)
        return allocation

    runner = replace(make_runner(allocation_factory=allocate, accepts_aggregate_executor_accounting=False), step_executor=Executor())
    source = '''@nh.function
async def compute() -> int:
    result: int = 0
    """execute
    Set <:result> to 7.
    """
    return result
'''
    cell_id_to_experiment = {name: _DefinitionExperiment() for name in ("first", "second")}
    cell_id_to_boundary: dict[str, NaturalCallBoundary] = {}
    for cell_id, experiment in cell_id_to_experiment.items():
        experiment.submit(source)
        cell_id_to_boundary[cell_id] = NaturalCallBoundary(
            runner,
            {function: int for function in experiment.function_to_approval},
            grant=Grant.of(cell_id=cell_id),
        )
    first_function = cell_id_to_experiment["first"].binding_name_to_value["compute"]
    second_function = cell_id_to_experiment["second"].binding_name_to_value["compute"]
    assert first_function is not second_function

    async def cell(cell_id: str) -> None:
        with function_call_scope(cell_id_to_boundary[cell_id]):
            code = compile("result = await compute()", f"<await-{cell_id}>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            await eval(code, cell_id_to_experiment[cell_id].binding_name_to_value)

    with function_call_scope(cell_id_to_boundary["second"]), pytest.raises(RunRejectedError):
        await first_function()
    assert cell_id_to_boundary["second"].handle_list == []
    first, second = asyncio.create_task(cell("first")), asyncio.create_task(cell("second"))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cell_id_to_started.values())), timeout=5)
        first_handle, second_handle = (cell_id_to_boundary[name].handle_list[0] for name in ("first", "second"))
        assert first_handle.run_id != second_handle.run_id
        assert len(allocation_list) == 2 and allocation_list[0] is not allocation_list[1]
        assert all(entry.event.kind != "run_end" for entry in first_handle.ledger.entries)
        assert all(entry.event.kind != "run_end" for entry in second_handle.ledger.entries)
        assert current_host() is None
        with nh.run(ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": 99})])):
            assert await _cooperative(0) == 99
        if first_outcome == "cancel":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            cell_id_to_release["first"].set()
            if first_outcome == "exhaust":
                with pytest.raises(RunFailedError):
                    await first
            else:
                await first
        assert not second.done()
        assert all(entry.event.kind != "run_end" for entry in second_handle.ledger.entries)
        cell_id_to_release["second"].set()
        await second
        assert sorted(request_cell_id_list) == ["first", "second"]
        first_event = first_handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
        second_event = second_handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event
        assert isinstance(first_event, NaturalExecutionOutcomeEvent) and isinstance(second_event, NaturalExecutionOutcomeEvent)
        assert first_event.executor_usage.request_count == (0 if first_outcome == "cancel" else 1)
        assert second_event.executor_usage.request_count == 1
        assert second_event.executor_usage.input_token_count == 17
        for handle in (first_handle, second_handle):
            assert [entry.event.kind for entry in handle.ledger.entries].count("run_end") == 1
        assert cell_id_to_experiment["second"].binding_name_to_value["result"] == 7
        assert ("result" in cell_id_to_experiment["first"].binding_name_to_value) == (first_outcome == "success")
        await cell("second")
        assert len(allocation_list) == 3
        assert cell_id_to_boundary["second"].handle_list[1].run_id != second_handle.run_id
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        for experiment in cell_id_to_experiment.values():
            experiment.close()
