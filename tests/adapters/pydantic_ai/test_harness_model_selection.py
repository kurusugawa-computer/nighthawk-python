from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.test import TestModel

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration, selected_configuration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.adapters.pydantic_ai.harness import model_runner
from nighthawk.adapters.pydantic_ai.model_outcome import ModelStepResponse
from nighthawk.core.model_selection import ModelSelection
from nighthawk.core.scoping import get_scope
from nighthawk.core.step_context import StepContext
from nighthawk.core.step_request import StepCandidate
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness._natural_execution import HostStepExecutor
from nighthawk.harness.errors import RunFailedError
from nighthawk.harness.ledger import NaturalExecutionOutcomeEvent, RunEndEvent, RunEntryEvent
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from tests.support.runs import DecisionsTransport, decision_model

_DECISION_MODEL = "openai-decisions:gpt-6-luna"


class _ConfigurableExecutor(ScriptedExecutor):
    def __init__(self, model: str, observations: list[str]) -> None:
        super().__init__(responses=[StepCandidate()] * 10)
        self.model = model
        self.observations = observations

    def resolve_model_selection(self, selection: ModelSelection) -> _ConfigurableExecutor:
        configuration = selected_configuration(selection, StepExecutorConfiguration(execution_model=self.model))
        assert isinstance(configuration.execution_model, str)
        return _ConfigurableExecutor(configuration.execution_model, self.observations)

    def run_step(self, **arguments: Any) -> Any:
        self.observations.append(self.model)
        return super().run_step(**arguments)


@nh.function
def _plain() -> None:
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Finish this step.
    """


def test_core_scope_preserves_a_configurable_executor_and_restores_parent() -> None:
    observations: list[str] = []
    executor = _ConfigurableExecutor("test:A", observations)
    with nh.run(executor):
        _plain()
        with nh.scope(execution_model="test:B"):
            assert isinstance(get_scope().step_executor, _ConfigurableExecutor)
            _plain()
        assert get_scope().step_executor is executor
        _plain()
    assert observations == ["test:A", "test:B", "test:A"]


def _child() -> None:
    with nh.scope(execution_model="test:B"):
        assert isinstance(get_scope().step_executor, HostStepExecutor)
        _plain()


@nh.function
async def _plain_async() -> None:
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Finish this step.
    """


async def _child_async() -> None:
    with nh.scope(execution_model="test:B"):
        assert isinstance(get_scope().step_executor, HostStepExecutor)
        await _plain_async()


@nh.function
def _supported(text: str) -> bool:
    supported: bool = False
    """decide <:supported>
    Does <text> state a supported claim?
    """
    return supported


class _ObservedModel(TestModel):
    def __init__(self, name: str, requests: list[str]) -> None:
        super().__init__(model_name=name, call_tools=[], custom_output_args={"result": {"kind": "pass"}})
        self.requests = requests

    async def request(self, *arguments: Any, **keyword_arguments: Any) -> ModelResponse:
        self.requests.append(self.model_name)
        return await super().request(*arguments, **keyword_arguments)


def _runner(
    requests: list[str],
    constructions: list[str],
    *,
    limit: int = 24,
    maximum_tokens: int | None = None,
    model_factory: Any = _ObservedModel,
    decisions: DecisionsTransport | None = None,
) -> Runner:
    """Permit (test:A, None), (test:B, None), and, with ``decisions``, both execution models paired with the decision model."""

    def factory(configuration: StepExecutorConfiguration) -> AgentStepExecutor:
        assert isinstance(configuration.execution_model, str)
        assert configuration.execution_model_settings == {"max_tokens": 4000}
        constructions.append(configuration.execution_model)
        if configuration.decision_model is not None:
            assert decisions is not None and configuration.decision_model == _DECISION_MODEL
            configuration = configuration.model_copy(update={"decision_model": decision_model(decisions)})
        return AgentStepExecutor.from_agent(
            agent=Agent(model_factory(configuration.execution_model, requests), output_type=ModelStepResponse, deps_type=StepContext),
            configuration=configuration,
        )

    decision_name_tuple = (None, _DECISION_MODEL) if decisions is not None else (None,)
    return model_runner(
        model_configuration_tuple=tuple(
            StepExecutorConfiguration(execution_model=execution_name, decision_model=decision_name, execution_model_settings={"max_tokens": 4000})
            for execution_name in ("test:A", "test:B")
            for decision_name in decision_name_tuple
        ),
        default_execution_model="test:A",
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(
            identity=identity.run_id, maximum_executor_step_count=limit, maximum_total_token_count=maximum_tokens
        ),
        executor_factory=factory,
    )


def _models(handle: RunHandle) -> list[str | None]:
    return [
        name
        for entry in handle.ledger.select(NaturalExecutionOutcomeEvent)
        if isinstance(entry.event, NaturalExecutionOutcomeEvent)
        for name in entry.event.response_model_name_tuple
    ]


@pytest.mark.parametrize("model_only", [False, True])
def test_runner_lazy_parent_child_scope_and_shared_accounting(model_only: bool) -> None:
    requests: list[str] = []
    constructions: list[str] = []
    runner = _runner(requests, constructions)
    assert constructions == []
    assert runner.run(ScaffoldComposition(RunContract(int, int), lambda value: value + 1), 6) == 7
    assert constructions == []

    def body(value: None) -> None:
        parent = get_scope().step_executor
        _plain()
        _child()
        _plain()
        selected_scope = (
            nh.scope(execution_model="test:B")
            if model_only
            else nh.scope(
                step_executor_configuration=StepExecutorConfiguration(execution_model="test:B", execution_model_settings={"max_tokens": 4000})
            )
        )
        with pytest.raises(LookupError), selected_scope:
            assert isinstance(get_scope().step_executor, HostStepExecutor)
            _plain()
            raise LookupError("restore")
        assert get_scope().step_executor is parent
        _plain()

    handle = RunHandle()
    runner.run(ScaffoldComposition(RunContract(type(None), type(None)), body), None, handle=handle)
    assert requests == ["test:A", "test:B", "test:A", "test:B", "test:A"]
    assert _models(handle) == requests
    assert constructions == ["test:A", "test:B"]
    assert handle.ledger.executor_step_count() == 5
    assert (
        sum(
            entry.event.executor_usage.request_count
            for entry in handle.ledger.select(NaturalExecutionOutcomeEvent)
            if isinstance(entry.event, NaturalExecutionOutcomeEvent)
        )
        == 5
    )
    assert len(handle.ledger.select(RunEntryEvent)) == len(handle.ledger.select(RunEndEvent)) == 1


async def test_async_selection_restores_on_cancellation_and_exception() -> None:
    requests: list[str] = []
    runner = _runner(requests, [])

    async def body(value: None) -> None:
        parent = get_scope().step_executor
        await _plain_async()
        await _child_async()
        await _plain_async()
        for error in (LookupError, asyncio.CancelledError):
            with pytest.raises(error), nh.scope(execution_model="test:B"):
                await _plain_async()
                raise error()
            assert get_scope().step_executor is parent
            await _plain_async()

    handle = RunHandle()
    pending = runner.run(AsyncScaffoldComposition(RunContract(type(None), type(None)), body), None, handle=handle)
    assert not requests and handle.run_id is None
    await pending
    assert requests == ["test:A", "test:B", "test:A", "test:B", "test:A", "test:B", "test:A"]
    assert _models(handle) == requests


@pytest.mark.parametrize(
    "selection",
    [
        lambda: nh.scope(execution_model="test:unknown"),
        lambda: nh.scope(execution_model=None),
        lambda: nh.scope(decision_model=_DECISION_MODEL),
        lambda: nh.scope(step_executor_configuration=StepExecutorConfiguration(execution_model="test:unknown")),
        lambda: nh.scope(step_executor_configuration=StepExecutorConfiguration(execution_model="test:B")),
        lambda: nh.scope(
            step_executor_configuration=StepExecutorConfiguration(execution_model="test:B", execution_model_settings={"max_tokens": 8000})
        ),
    ],
)
def test_unconfigured_selection_precedes_provider_access(selection: Callable[[], AbstractContextManager[object]]) -> None:
    requests: list[str] = []
    constructions: list[str] = []
    runner = _runner(requests, constructions)

    def body(value: None) -> None:
        with selection():
            _plain()

    with pytest.raises(RunFailedError):
        runner.run(ScaffoldComposition(RunContract(type(None), type(None)), body), None)
    assert requests == constructions == []


def test_decision_selection_replaces_only_the_decision_model_of_the_current_pair() -> None:
    requests: list[str] = []
    constructions: list[str] = []
    decisions = DecisionsTransport()
    runner = _runner(requests, constructions, decisions=decisions)

    def body(value: None) -> None:
        with nh.scope(decision_model=_DECISION_MODEL):
            assert _supported("The sky is blue.") is True
            _plain()
            with nh.scope(execution_model="test:B"):
                assert _supported("Water is wet.") is True
                _plain()
        _plain()
        with pytest.raises(RunFailedError):
            _supported("A decision needs a selected decision model.")

    handle = RunHandle()
    with pytest.raises(RunFailedError):
        runner.run(ScaffoldComposition(RunContract(type(None), type(None)), body), None, handle=handle)
    assert requests == ["test:A", "test:B", "test:A"]
    assert len(decisions.request_body_list) == 2
    assert constructions == ["test:A", "test:B", "test:A"]
    outcome_list = [
        entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent) if isinstance(entry.event, NaturalExecutionOutcomeEvent)
    ]
    assert [(event.block_kind, event.status) for event in outcome_list] == [
        ("decide", "success"),
        ("execute", "success"),
        ("decide", "success"),
        ("execute", "success"),
        ("execute", "success"),
        ("decide", "failure"),
    ]
    assert [event.response_model_name_tuple for event in outcome_list if event.block_kind == "decide"] == [("gpt-6-luna",), ("gpt-6-luna",), ()]


@pytest.mark.parametrize("by_tokens", [False, True])
def test_parent_consumption_prevents_child_request(by_tokens: bool) -> None:
    requests: list[str] = []
    runner = _runner(requests, [], limit=24 if by_tokens else 1, maximum_tokens=1 if by_tokens else None)

    def body(value: None) -> None:
        _plain()
        _child()

    handle = RunHandle()
    with pytest.raises(RunFailedError):
        runner.run(ScaffoldComposition(RunContract(type(None), type(None)), body), None, handle=handle)
    assert requests == ["test:A"]
    assert _models(handle) == requests
    assert len(handle.ledger.select(RunEndEvent)) == 1


async def test_nested_provider_calls_restore_enclosing_model_and_account_once() -> None:
    class NestingModel(_ObservedModel):
        entered = False

        async def request(self, *arguments: Any, **keyword_arguments: Any) -> ModelResponse:
            if self.model_name == "test:A" and not self.entered:
                self.entered = True
                await _child_async()
            return await super().request(*arguments, **keyword_arguments)

    requests: list[str] = []
    handle = RunHandle()
    runner = _runner(requests, [], model_factory=NestingModel)

    async def body(value: None) -> None:
        await _plain_async()
        await _plain_async()

    await runner.run(AsyncScaffoldComposition(RunContract(type(None), type(None)), body), None, handle=handle)
    assert requests == ["test:B", "test:A", "test:A"]
    assert _models(handle) == requests
    assert handle.ledger.executor_step_count() == 3


async def test_shared_runner_sessions_keep_model_context_and_usage_separate() -> None:
    from nighthawk.session import CellInput, SessionManager

    entered = {name: asyncio.Event() for name in ("test:A", "test:B")}
    release = {name: asyncio.Event() for name in ("test:A", "test:B")}

    class WaitingModel(_ObservedModel):
        async def request(self, *arguments: Any, **keyword_arguments: Any) -> ModelResponse:
            entered[self.model_name].set()
            await release[self.model_name].wait()
            return await super().request(*arguments, **keyword_arguments)

    requests: list[str] = []
    runner = _runner(requests, [], model_factory=WaitingModel)
    first, second = SessionManager(runner=runner).create_session(), SessionManager(runner=runner).create_session()
    source = '''@nh.function
async def answer() -> int:
    result: int = 0
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Finish this step, keeping <:result> at zero.
    """
    return result
'''
    for session in (first, second):
        assert (await session.submit(CellInput(source, mode="python")).wait_async()).status == "success"
    selected_source = '''@nh.function
async def answer() -> int:
    result: int = 0
    with nh.scope(execution_model="test:B"):
        """execute
        ---
        deny: [return, raise, break, continue]
        ---
        Finish this step, keeping <:result> at zero.
        """
    return result
'''
    assert (await first.submit(CellInput(selected_source, mode="python")).wait_async()).status == "success"
    first_execution = first.submit(CellInput("> result = await answer()"))
    second_execution = second.submit(CellInput("> result = await answer()"))
    tasks = [asyncio.create_task(execution.wait_async()) for execution in (first_execution, second_execution)]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 5)
        assert first_execution.run_id_tuple != second_execution.run_id_tuple
        first_execution.request_interrupt()
        assert (await tasks[0]).status == "cancelled"
        assert not tasks[1].done()
        with nh.run(ScriptedExecutor(responses=[StepCandidate()])):
            await _plain_async()
        release["test:A"].set()
        assert (await tasks[1]).status == "success"
        assert requests == ["test:A"]
        first_events = first._run_id_to_ledger[first_execution.run_id_tuple[0]].select(NaturalExecutionOutcomeEvent)
        second_events = second._run_id_to_ledger[second_execution.run_id_tuple[0]].select(NaturalExecutionOutcomeEvent)
        assert isinstance(first_events[0].event, NaturalExecutionOutcomeEvent)
        assert isinstance(second_events[0].event, NaturalExecutionOutcomeEvent)
        assert first_events[0].event.executor_usage.request_count == 0
        assert second_events[0].event.response_model_name_tuple == ("test:A",)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        first.close()
        second.close()
