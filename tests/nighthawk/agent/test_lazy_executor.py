"""Library provider laziness, per-loop delegates, host-owned provider communication, and per-request accounting."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.test import TestModel

import nighthawk as nh
from nighthawk import function_call_scope
from nighthawk.agent import NaturalExecutionOutcomeEvent, PerimeterEffectRejectedError, RunContract, RunFailedError, RunHandle, ScaffoldComposition
from nighthawk.agent._natural_execution import executor_step_observation_of
from nighthawk.agent.runtime import ResourceConsumption, ResourceRejection, ResourceRequest
from nighthawk.runtime.model_outcome import ModelStepResponse

from .conftest import make_runner
from .lazy_executor_support import _answer, _run
from .milestone_zero_support import LazyReportingExecutor, NaturalCallBoundary


class _RecordingAllocation:
    identity = "milestone-zero-recording"

    def __init__(self) -> None:
        self.consumption_list: list[ResourceConsumption] = []

    def admit(self, request: ResourceRequest) -> ResourceRejection | None:
        return None

    def record(self, consumption: ResourceConsumption) -> None:
        self.consumption_list.append(consumption)


class _ObservedModel(TestModel):
    def __init__(self, allocation: _RecordingAllocation) -> None:
        super().__init__(call_tools=["nh_evaluate"], custom_output_args={"result": {"kind": "pass"}})
        self.allocation = allocation
        self.prior_observed_count_list: list[int] = []

    async def request(self, *arguments: Any, **keyword_arguments: Any) -> ModelResponse:
        self.prior_observed_count_list.append(sum(consumption.executor_usage.request_count for consumption in self.allocation.consumption_list))
        return await super().request(*arguments, **keyword_arguments)


def _scripted_executor(model: TestModel) -> nh.AgentStepExecutor:
    return nh.AgentStepExecutor.from_agent(agent=Agent(model, output_type=ModelStepResponse, deps_type=nh.StepContext))


def test_provider_stays_lazy_through_configuration_and_python_only_run() -> None:
    import pydoc

    from .session.test_source_adoption import _DefinitionExperiment

    def unavailable() -> nh.AgentStepExecutor:
        raise AssertionError("provider construction is not allowed")

    executor = LazyReportingExecutor(unavailable)
    assert "LazyReportingExecutor" in pydoc.render_doc(executor)
    assert executor_step_observation_of(executor) == "executor_reports"
    assert callable(_answer)
    assert (
        replace(make_runner(accepts_aggregate_executor_accounting=False), step_executor=executor).run(
            ScaffoldComposition(RunContract(int, int), lambda value: value + 1), 6
        )
        == 7
    )
    assert executor.construction_count == 0
    experiment = _DefinitionExperiment()
    try:
        with function_call_scope(NaturalCallBoundary(replace(make_runner(), step_executor=executor), {})):
            experiment.submit("@nh.function\ndef defined() -> int:\n    return 7\n")
        assert callable(experiment.binding_name_to_value["defined"])
        assert executor.construction_count == 0
    finally:
        experiment.close()


def test_lazy_executor_reports_each_request_immediately_without_aggregate_or_double_accounting() -> None:
    handle = RunHandle()
    allocation = _RecordingAllocation()
    model = _ObservedModel(allocation)
    executor = LazyReportingExecutor(lambda: _scripted_executor(model))
    assert _run(executor, handle, calls=2, allocation=allocation) == 0
    assert executor.construction_count == 1
    assert model.prior_observed_count_list == [0, 1, 2, 3]
    assert handle.ledger.executor_step_count() == 4
    assert sum(consumption.executor_usage.request_count for consumption in allocation.consumption_list) == 4
    outcome_list = [entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent)]
    assert len(outcome_list) == 2
    assert all(isinstance(outcome, NaturalExecutionOutcomeEvent) and outcome.executor_step_count == 2 for outcome in outcome_list)
    assert sum(outcome.executor_usage.request_count for outcome in outcome_list if isinstance(outcome, NaturalExecutionOutcomeEvent)) == 4


def test_lazy_executor_exhaustion_prevents_the_next_actual_provider_request() -> None:
    handle = RunHandle()
    model = _ObservedModel(_RecordingAllocation())
    executor = LazyReportingExecutor(lambda: _scripted_executor(model))
    with pytest.raises(RunFailedError):
        _run(executor, handle, limit=1)
    assert model.prior_observed_count_list == [0]
    assert handle.ledger.executor_step_count() == 1
    assert any(entry.event.kind == "resource_exhaustion" for entry in handle.ledger.entries)
    assert [entry.event.kind for entry in handle.ledger.entries].count("run_end") == 1


def test_a_nested_call_inside_a_running_request_receives_its_own_delegate_without_double_charging_usage() -> None:
    class NestingModel(_ObservedModel):
        entered = False

        def __init__(self, allocation: _RecordingAllocation) -> None:
            super().__init__(allocation)
            self.loop_set: set[asyncio.AbstractEventLoop] = set()

        async def request(self, *arguments: Any, **keyword_arguments: Any) -> ModelResponse:
            self.loop_set.add(asyncio.get_running_loop())
            if not NestingModel.entered:
                NestingModel.entered = True
                assert _answer() == 0
            return await super().request(*arguments, **keyword_arguments)

    handle = RunHandle()
    allocation = _RecordingAllocation()
    model_list: list[NestingModel] = []

    def factory() -> nh.AgentStepExecutor:
        model_list.append(NestingModel(allocation))
        return _scripted_executor(model_list[-1])

    executor = LazyReportingExecutor(factory)
    assert _run(executor, handle, allocation=allocation) == 0
    assert executor.construction_count == 2
    outer, inner = model_list
    assert len(outer.loop_set) == len(inner.loop_set) == 1 and outer.loop_set.isdisjoint(inner.loop_set)
    assert (inner.prior_observed_count_list, outer.prior_observed_count_list) == ([0, 1], [2, 3])
    assert handle.ledger.executor_step_count() == 4
    outcome_list = [entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent)]
    assert len(outcome_list) == 2
    assert all(isinstance(outcome, NaturalExecutionOutcomeEvent) and outcome.executor_usage.request_count == 2 for outcome in outcome_list)
    assert sum(consumption.executor_usage.request_count for consumption in allocation.consumption_list) == 4


def test_provider_communication_is_host_infrastructure_only_during_the_admitted_request() -> None:
    class AuditProbeModel(_ObservedModel):
        async def request(self, *arguments: Any, **keyword_arguments: Any) -> ModelResponse:
            # Exercise the hook without opening a socket, a file, or a process.
            sys.audit("socket.connect", None, ("provider.invalid", 443))
            sys.audit("open", "/dev/null", "w", os.O_WRONLY)
            sys.audit("subprocess.Popen", "uname", ["uname", "-p"], None, None)
            return await super().request(*arguments, **keyword_arguments)

    executor = LazyReportingExecutor(lambda: _scripted_executor(AuditProbeModel(_RecordingAllocation())))

    def body(value: int) -> int:
        with pytest.raises(PerimeterEffectRejectedError):
            sys.audit("socket.connect", None, ("provider.invalid", 443))
        result = _answer()
        with pytest.raises(PerimeterEffectRejectedError):
            sys.audit("socket.connect", None, ("provider.invalid", 443))
        return result

    runner = replace(make_runner(accepts_aggregate_executor_accounting=False), step_executor=executor)
    assert runner.run(ScaffoldComposition(RunContract(int, int), body), 0) == 0
    assert executor.construction_count == 1
