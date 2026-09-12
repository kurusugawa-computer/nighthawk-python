from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

import nighthawk as nh
from examples.embedded_execution import PreservingExecutor, RecordingHandler, RequestObserver, deterministic_model
from tests.support.executors import StubExecutor


def test_selection_is_inert_and_validates_exclusive_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    constructions: list[object] = []
    monkeypatch.setattr("nighthawk.runtime.step_executor._new_agent_step_executor", lambda configuration: constructions.append(configuration))

    @nh.natural_function(model="openai:inert")
    def function() -> None:
        raise AssertionError("body should be refused")

    assert nh.get_natural_function_definition(function) is not None
    handler = RecordingHandler()
    handler.refuse = True
    with nh.function_call_scope(handler), pytest.raises(PermissionError):
        function()
    selection = handler.calls[0].model_selection
    assert selection is not None and selection.model == "openai:inert"
    assert not constructions
    with pytest.raises(ValueError, match="exactly one"):
        nh.natural_function(model="test:A", step_executor_configuration=nh.StepExecutorConfiguration())
    with (
        nh.run(StubExecutor()),
        pytest.raises(ValueError, match="exactly one"),
        nh.scope(model="test:A", step_executor_configuration=nh.StepExecutorConfiguration()),
    ):
        pass
    with pytest.raises(ValueError):
        nh.ModelSelection()
    with pytest.raises(TypeError):
        nh.ModelSelection(model=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_model_selection_preserves_controls_and_unwinds_before_handler(outcome: str) -> None:
    first, second = deterministic_model("A"), deterministic_model("B")
    observer = RequestObserver()
    configuration = nh.StepExecutorConfiguration(model=first, model_settings={"temperature": 0.25}, user_prompt_suffix_fragments=("inherited",))
    executor = PreservingExecutor(nh.AgentStepExecutor.from_configuration(configuration=configuration), {"allowed:B": second}, observer)
    selected: list[Any] = []
    meter = nh.UsageMeter()
    oversight = nh.oversight.Oversight()
    lifecycle = nh.lifecycle.StepLifecycle()

    class Handler(RecordingHandler):
        async def invoke_async(self, call: nh.FunctionCall, proceed: Callable) -> object:
            assert nh.get_step_executor() is executor
            assert call.model_selection is not None
            try:
                return await super().invoke_async(call, proceed)
            finally:
                assert nh.get_step_executor() is executor

    @nh.natural_function(model="allowed:B")
    async def child() -> None:
        active = nh.get_step_executor()
        assert isinstance(active, PreservingExecutor)
        selected.append(active.executor.configuration.model)
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
        with nh.scope(model=nh.UNSET):
            assert nh.get_step_executor() is executor
        replacement = nh.StepExecutorConfiguration(model=second)
        with nh.scope(step_executor_configuration=replacement) as replaced:
            assert isinstance(replaced, PreservingExecutor)
            assert replaced.executor.configuration is replacement
            assert replaced.executor.configuration.model_settings is None
    assert selected == [second]


def test_unsupported_resolvers_and_subclasses_fail_without_unwrapping() -> None:
    with nh.run(StubExecutor()), pytest.raises(nh.NighthawkError, match="ConfigurableStepExecutor"), nh.scope(model="test:B"):
        pass

    class Invalid(StubExecutor):
        def resolve_model_selection(self, selection: nh.ModelSelection) -> Any:
            return object()

    with nh.run(Invalid()), pytest.raises(nh.NighthawkError, match="return a StepExecutor"), nh.scope(model="test:B"):
        pass

    class Custom(nh.AgentStepExecutor):
        pass

    custom = Custom(configuration=nh.StepExecutorConfiguration(model=deterministic_model("A")))
    with nh.run(custom), pytest.raises(nh.NighthawkError, match="subclasses"), nh.scope(model="test:B"):
        pass


def test_rejected_model_and_explicit_executor_replacement() -> None:
    observer = RequestObserver()
    model = deterministic_model("A")
    executor = PreservingExecutor(nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=model)), {}, observer)
    with nh.run(executor), nh.scope(capabilities=[observer]):
        with pytest.raises(PermissionError), nh.scope(model="not:allowed"):
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
    parent = nh.StepExecutorConfiguration(model=first, model_settings={"temperature": 0.5}, user_prompt_suffix_fragments=("parent",))
    replacement = nh.StepExecutorConfiguration(model=second)
    executor = nh.AgentStepExecutor.from_configuration(configuration=parent)
    handler = RecordingHandler()

    def check_configuration() -> None:
        active = nh.get_step_executor()
        assert isinstance(active, nh.AgentStepExecutor)
        assert active.configuration is replacement
        assert active.configuration.model is second
        assert active.configuration.model_settings is None
        assert active.configuration.user_prompt_suffix_fragments == ()

    @nh.natural_function(step_executor_configuration=replacement)
    def function() -> None:
        check_configuration()

    @nh.natural_function(step_executor_configuration=replacement)
    async def async_function() -> None:
        check_configuration()

    with nh.function_call_scope(handler), nh.run(executor):
        if asynchronous:
            asyncio.run(async_function())
        else:
            function()
        assert nh.get_step_executor() is executor
    selection = handler.calls[0].model_selection
    assert selection is not None and selection.configuration is replacement
