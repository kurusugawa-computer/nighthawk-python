from __future__ import annotations

import asyncio
import contextvars
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

import nighthawk as nh
from tests.support.executors import StubExecutor


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[nh.FunctionCall] = []
        self.events: list[str] = []
        self.refuse = False

    def enter(self, call: nh.FunctionCall) -> None:
        assert nh.get_function_call() is call
        self.calls.append(call)
        self.events.append("enter")
        if self.refuse:
            raise PermissionError("refused")

    def invoke(self, call: nh.FunctionCall, proceed: Callable[[], object]) -> object:
        self.enter(call)
        try:
            return proceed()
        finally:
            self.events.append("exit")

    async def invoke_async(self, call: nh.FunctionCall, proceed: Callable[[], Awaitable[object]]) -> object:
        self.enter(call)
        try:
            return await proceed()
        finally:
            self.events.append("exit")


def test_refusal_alias_arguments_and_finally_without_blocks() -> None:
    handler = RecordingHandler()
    effects: list[str] = []

    @nh.function
    def function(value: object, *, fail: bool = False) -> object:
        effects.append("body")
        try:
            if fail:
                raise ValueError("before any block")
            return value
        finally:
            effects.append("finally")
            assert handler.events[-1] == "enter"

    alias = function
    value = object()
    with nh.function_call_scope(handler):
        handler.refuse = True
        with pytest.raises(PermissionError):
            alias(value)
        assert effects == []
        assert nh.get_function_call() is None
        handler.refuse = False
        with nh.run(StubExecutor()):
            assert alias(value) is value
        with pytest.raises(ValueError):
            alias(value, fail=True)
        assert effects == ["body", "finally"] * 2
    call = handler.calls[-1]
    assert call.arguments[0] is value
    assert call.keyword_name_to_value["fail"] is True
    with pytest.raises(TypeError):
        call.keyword_name_to_value["fail"] = False  # type: ignore[index]
    assert nh.get_function_call() is None


def test_recursive_and_mixed_parentage_and_explicit_clear() -> None:
    handler = RecordingHandler()

    @nh.function
    def recursive(depth: int) -> int:
        return recursive(depth - 1) + 1 if depth else 0

    @nh.function
    async def parent() -> int:
        return recursive(2)

    with nh.function_call_scope(handler):
        assert asyncio.run(parent()) == 2
        with nh.function_call_scope(None):
            assert recursive(0) == 0
    assert len(handler.calls) == 4
    assert handler.calls[0].parent_call_id is None
    for previous, call in zip(handler.calls, handler.calls[1:], strict=False):
        assert call.parent_call_id == previous.call_id
    assert len({call.call_id for call in handler.calls}) == 4


def test_async_uses_execution_context_and_task_context() -> None:
    first, second = RecordingHandler(), RecordingHandler()

    @nh.function
    async def function() -> int:
        return 7

    async def execute() -> None:
        with nh.function_call_scope(first):
            coroutine = function()
        with nh.function_call_scope(second):
            assert await coroutine == 7
            task = asyncio.create_task(function(), context=contextvars.Context())
        with nh.function_call_scope(first):
            assert await task == 7
        assert not first.calls
        assert len(second.calls) == 1

    asyncio.run(execute())


def test_sync_continuation_guard_and_return_coroutine_identity() -> None:
    saved: list[Callable[[], object]] = []

    class Handler(RecordingHandler):
        def invoke(self, call: nh.FunctionCall, proceed: Callable[[], object]) -> object:
            saved.append(proceed)
            result = proceed()
            with pytest.raises(nh.FunctionCallError, match="once"):
                proceed()
            return result

    async def body() -> int:
        return 4

    coroutine = body()

    @nh.function
    def function() -> Any:
        return coroutine

    try:
        with nh.function_call_scope(Handler()):
            assert function() is coroutine
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CREATED
        with pytest.raises(nh.FunctionCallError, match="active"):
            saved[0]()
    finally:
        coroutine.close()


@pytest.mark.parametrize("mode", ["skip", "discard", "twice", "task", "thread"])
def test_invalid_async_handlers(mode: str) -> None:
    effects: list[str] = []
    saved: list[Callable[[], Awaitable[object]]] = []
    discarded: list[Any] = []

    class Handler(RecordingHandler):
        async def invoke_async(self, call: nh.FunctionCall, proceed: Callable[[], Awaitable[object]]) -> object:
            saved.append(proceed)
            if mode == "skip":
                return None
            if mode == "discard":
                discarded.append(proceed())
                return None
            if mode == "twice":
                await proceed()
                return await proceed()
            if mode == "thread":
                return await asyncio.to_thread(lambda: asyncio.run(proceed()))  # type: ignore[arg-type]
            return await asyncio.create_task(proceed())  # type: ignore[arg-type]

    @nh.function
    async def function() -> None:
        effects.append("body")

    with nh.function_call_scope(Handler()), pytest.raises(nh.FunctionCallError):
        asyncio.run(function())
    assert effects == (["body"] if mode == "twice" else [])
    for coroutine in discarded:
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
    with pytest.raises(nh.FunctionCallError, match="active"):
        saved[0]()


def test_generators_rejected_before_body() -> None:
    @nh.function
    def generator():
        yield 1

    @nh.function
    async def async_generator():
        yield 1

    with nh.function_call_scope(RecordingHandler()):
        for function in (generator, async_generator):
            with pytest.raises(nh.FunctionCallError, match="generator"):
                function()


def test_completed_block_delivery_failure_is_observed_after_function_finally() -> None:
    handler = RecordingHandler()
    terminal: list[nh.lifecycle.StepFinished] = []
    effects: list[str] = []
    error = ValueError("host storage failed")

    def finished(event: nh.lifecycle.StepFinished) -> None:
        terminal.append(event)
        raise error

    @nh.function
    def function() -> None:
        try:
            """execute
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
            """
        finally:
            effects.append("finally")
            assert handler.events == ["enter"]

    with nh.function_call_scope(handler), nh.run(StubExecutor()), nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=finished)):
        with pytest.raises(ValueError) as caught:
            function()
        assert caught.value is error
    assert len(terminal) == 1 and isinstance(terminal[0], nh.lifecycle.StepCompleted)
    assert effects == ["finally"]
    assert handler.events == ["enter", "exit"]


def test_handler_can_establish_run_and_observe_outer_alias() -> None:
    from functools import wraps

    class Handler(RecordingHandler):
        def invoke(self, call: nh.FunctionCall, proceed: Callable[[], object]) -> object:
            with nh.run(StubExecutor()):
                return super().invoke(call, proceed)

    @nh.function
    def function() -> int:
        """execute
        {"step_outcome": {"kind": "return", "return_expression": "3"}, "bindings": {}}
        """
        return 0

    @wraps(function)
    def outer() -> int:
        return function()

    handler = Handler()
    with nh.function_call_scope(handler):
        assert outer() == 3
    assert len(handler.calls) == 1
    assert handler.calls[0].definition.function is function
    assert nh.get_function_definition(outer) is None


def test_awaited_child_finally_finishes_before_parent_handler_returns() -> None:
    async def execute() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        handler = RecordingHandler()

        @nh.function
        async def child() -> int:
            try:
                return 4
            finally:
                entered.set()
                await release.wait()

        @nh.function
        async def parent() -> int:
            return await asyncio.create_task(child())

        with nh.function_call_scope(handler):
            task = asyncio.create_task(parent())
            try:
                await entered.wait()
                assert not task.done()
                assert handler.events == ["enter", "enter"]
            finally:
                release.set()
            assert await task == 4
        assert handler.events == ["enter", "enter", "exit", "exit"]
        assert handler.calls[1].parent_call_id == handler.calls[0].call_id

    asyncio.run(execute())
