from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

import nighthawk as nh
from nighthawk.runtime.scoping import _current_task_references
from nighthawk.testing import ScriptedExecutor
from tests.support.executors import StubExecutor
from tests.support.host_controls import PreservingExecutor, RecordingHandler, RequestObserver, block, deterministic_model


def test_selection_is_inert_and_validates_exclusive_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    constructions: list[object] = []
    monkeypatch.setattr("nighthawk.runtime.step_executor._new_agent_step_executor", lambda configuration: constructions.append(configuration))

    @nh.function
    def function() -> None:
        with nh.scope(execution_model="openai:inert"):
            raise AssertionError("body should be refused")

    assert nh.get_function_definition(function) is not None
    handler = RecordingHandler()
    handler.refuse = True
    with nh.function_call_scope(handler), pytest.raises(PermissionError):
        function()
    assert not constructions
    with pytest.raises(ValueError, match="exclusively"):
        nh.ModelSelection(execution_model="test:A", configuration=nh.StepExecutorConfiguration())
    with (
        nh.run(StubExecutor()),
        pytest.raises(ValueError, match="exclusively"),
        nh.scope(execution_model="test:A", step_executor_configuration=nh.StepExecutorConfiguration()),
    ):
        pass
    with pytest.raises(ValueError):
        nh.ModelSelection()
    assert nh.ModelSelection(execution_model=None).apply(nh.StepExecutorConfiguration()).execution_model is None


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_model_selection_preserves_controls_and_unwinds_before_handler(outcome: str) -> None:
    first, second = deterministic_model("A"), deterministic_model("B")
    observer = RequestObserver()
    configuration = nh.StepExecutorConfiguration(
        execution_model=first, model_settings={"temperature": 0.25}, user_prompt_suffix_fragments=("inherited",)
    )
    executor = PreservingExecutor(nh.AgentStepExecutor.from_configuration(configuration=configuration), {"allowed:B": second}, observer)
    selected: list[Any] = []
    meter = nh.UsageMeter()
    oversight = nh.oversight.Oversight()
    lifecycle = nh.lifecycle.StepLifecycle()

    class Handler(RecordingHandler):
        async def invoke_async(self, call: nh.FunctionCall, proceed: Callable) -> object:
            assert nh.get_step_executor() is executor
            try:
                return await super().invoke_async(call, proceed)
            finally:
                assert nh.get_step_executor() is executor

    @nh.function
    async def child() -> None:
        with nh.scope(execution_model="allowed:B"):
            active = nh.get_step_executor()
            assert isinstance(active, PreservingExecutor)
            selected.append(active.executor.configuration.execution_model)
            assert active.observer is observer
            assert active.executor.configuration.model_settings == {"temperature": 0.25}
            assert active.executor.configuration.user_prompt_suffix_fragments == ("inherited",)
            assert nh.get_usage_meter() is meter
            assert nh.get_capabilities() == (observer,)
            assert nh.get_oversight() is oversight
            assert nh.get_lifecycle() is lifecycle
            if outcome == "failure":
                raise ValueError("failed child")
            if outcome == "cancel":
                raise asyncio.CancelledError("cancelled child")

    with (
        nh.function_call_scope(Handler()),
        nh.run(executor, usage_meter=meter),
        nh.scope(capabilities=[observer], oversight=oversight, lifecycle=lifecycle),
    ):
        if outcome == "success":
            asyncio.run(child())
        else:
            with pytest.raises(ValueError if outcome == "failure" else asyncio.CancelledError):
                asyncio.run(child())
        assert nh.get_step_executor() is executor
        with nh.scope(execution_model=nh.UNSET):
            assert nh.get_step_executor() is executor
        replacement = nh.StepExecutorConfiguration(execution_model=second)
        with nh.scope(step_executor_configuration=replacement) as replaced:
            assert isinstance(replaced, PreservingExecutor)
            assert replaced.executor.configuration is replacement
            assert replaced.executor.configuration.model_settings == {}
    assert selected == [second]


def test_unsupported_resolvers_and_subclasses_fail_without_unwrapping() -> None:
    with nh.run(StubExecutor()), pytest.raises(nh.NighthawkError, match="ConfigurableStepExecutor"), nh.scope(execution_model="test:B"):
        pass

    class Invalid(StubExecutor):
        def resolve_model_selection(self, selection: nh.ModelSelection) -> Any:
            return object()

    with nh.run(Invalid()), pytest.raises(nh.NighthawkError, match="return a StepExecutor"), nh.scope(execution_model="test:B"):
        pass

    class Custom(nh.AgentStepExecutor):
        pass

    custom = Custom(configuration=nh.StepExecutorConfiguration(execution_model=deterministic_model("A")))
    with nh.run(custom), pytest.raises(nh.NighthawkError, match="subclasses"), nh.scope(execution_model="test:B"):
        pass


def test_rejected_model_and_explicit_executor_replacement() -> None:
    observer = RequestObserver()
    model = deterministic_model("A")
    executor = PreservingExecutor(
        nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(execution_model=model)), {}, observer
    )
    with nh.run(executor), nh.scope(capabilities=[observer]):
        with pytest.raises(PermissionError), nh.scope(execution_model="not:allowed"):
            pass
        assert nh.get_step_executor() is executor
        assert observer.records == []
        replacement = StubExecutor()
        with nh.scope(step_executor=replacement, capabilities=[]):
            assert nh.get_step_executor() is replacement
            assert nh.get_capabilities() == ()
        with nh.run(replacement):
            assert nh.get_capabilities() == ()
        assert nh.get_capabilities() == (observer,)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_full_function_configuration_replaces_defaults_and_restores(asynchronous: bool) -> None:
    first, second = deterministic_model("A"), deterministic_model("B")
    parent = nh.StepExecutorConfiguration(execution_model=first, model_settings={"temperature": 0.5}, user_prompt_suffix_fragments=("parent",))
    replacement = nh.StepExecutorConfiguration(execution_model=second)
    executor = nh.AgentStepExecutor.from_configuration(configuration=parent)
    handler = RecordingHandler()

    def check_configuration() -> None:
        active = nh.get_step_executor()
        assert isinstance(active, nh.AgentStepExecutor)
        assert active.configuration is replacement
        assert active.configuration.execution_model is second
        assert active.configuration.model_settings == {}
        assert active.configuration.user_prompt_suffix_fragments == ()

    @nh.function
    def function() -> None:
        with nh.scope(step_executor_configuration=replacement):
            check_configuration()

    @nh.function
    async def async_function() -> None:
        with nh.scope(step_executor_configuration=replacement):
            check_configuration()

    with nh.function_call_scope(handler), nh.run(executor):
        if asynchronous:
            asyncio.run(async_function())
        else:
            function()
        assert nh.get_step_executor() is executor
    assert len(handler.calls) == 1


def test_concurrent_model_selection_and_actual_cancellation_restore_each_context() -> None:
    async def execute() -> None:
        first, second, third = (deterministic_model(name) for name in ("A", "B", "C"))
        observer = RequestObserver(threshold=1000)
        handler = RecordingHandler()
        executor = PreservingExecutor(
            nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(execution_model=first)),
            {"allowed:B": second, "allowed:C": third},
            observer,
        )
        child_entered, sibling_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        selected: list[object] = []

        @nh.function
        def inherited_prefix() -> None:
            active = nh.get_step_executor()
            assert isinstance(active, PreservingExecutor)
            assert _current_task_references()["policy"] is observer
            selected.append(active.executor.configuration.execution_model)
            block()

        @nh.function
        async def child() -> None:
            with nh.scope(execution_model="allowed:B"):
                inherited_prefix()
                child_entered.set()
                try:
                    await release.wait()
                finally:
                    inherited_prefix()

        alias = child

        async def invoke_child() -> None:
            try:
                await alias()
            finally:
                assert nh.get_step_executor() is executor
                assert nh.get_function_call() is None

        async def sibling() -> None:
            with nh.scope(execution_model="allowed:C"):
                inherited_prefix()
                sibling_entered.set()
                await release.wait()
                inherited_prefix()
            assert nh.get_step_executor() is executor

        with nh.function_call_scope(handler), nh.run(executor), nh.scope(capabilities=[observer], task_references={"policy": observer}):
            child_task, sibling_task = asyncio.create_task(invoke_child()), asyncio.create_task(sibling())
            try:
                await child_entered.wait()
                await sibling_entered.wait()
                assert nh.get_step_executor() is executor
                child_task.cancel("cancel selected child")
                with pytest.raises(asyncio.CancelledError, match="cancel selected child"):
                    await child_task
                assert not sibling_task.done()
            finally:
                release.set()
                await asyncio.gather(child_task, sibling_task, return_exceptions=True)
            sibling_task.result()
            inherited_prefix()
            assert nh.get_step_executor() is executor
        assert selected == [second, third, second, third, first]
        assert [record.configured_model for record in observer.records] == ["B", "C", "B", "C", "A"]
        assert observer.observed_tokens == 300
        assert len({record.request_id for record in observer.records}) == 5
        assert all(record.call_id and record.step_execution_id for record in observer.records)

    asyncio.run(execute())


def test_function_scope_requires_a_model_selection_resolver() -> None:
    with nh.run(ScriptedExecutor()):

        @nh.function
        def f() -> None:
            with nh.scope(step_executor_configuration=nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-5.6-luna")):
                pass

        with pytest.raises(nh.NighthawkError, match="ConfigurableStepExecutor"):
            f()
