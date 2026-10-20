"""Optional host interception of an entire Natural function invocation."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from .composition import UNSET, UnsetType
from .errors import NighthawkError
from .natural.definition import FunctionDefinition
from .ulid import generate_ulid


@dataclass(frozen=True)
class FunctionCall:
    """One invocation, with shallow immutable argument structure and live values."""

    call_id: str
    parent_call_id: str | None
    definition: FunctionDefinition
    arguments: tuple[object, ...]
    keyword_name_to_value: Mapping[str, object]


@runtime_checkable
class FunctionCallHandler(Protocol):
    """Application-owned admission, grouping, and observation of function calls."""

    def invoke(self, call: FunctionCall, proceed: Callable[[], object]) -> object: ...

    async def invoke_async(self, call: FunctionCall, proceed: Callable[[], Awaitable[object]]) -> object: ...


class FunctionCallError(NighthawkError):
    """A handler violated the function continuation or lifetime contract."""


_handler: ContextVar[FunctionCallHandler | None] = ContextVar("nighthawk_function_call_handler", default=None)
_call: ContextVar[FunctionCall | None] = ContextVar("nighthawk_function_call", default=None)


@contextmanager
def function_call_scope(handler: FunctionCallHandler | None | UnsetType = UNSET) -> Iterator[None]:
    """Inherit, replace, or clear interception independently of executor runs.

    Omission or UNSET inherits; None clears; a handler replaces the current one.
    Invalid handlers fail before context installation.
    """
    if (
        not isinstance(handler, UnsetType)
        and handler is not None
        and (not isinstance(handler, FunctionCallHandler) or not callable(handler.invoke) or not callable(handler.invoke_async))
    ):
        raise TypeError("handler must implement FunctionCallHandler, be None, or be UNSET")
    token = _handler.set(_handler.get() if isinstance(handler, UnsetType) else handler)
    try:
        yield
    finally:
        _handler.reset(token)


def get_function_call() -> FunctionCall | None:
    """Return the invocation active in this execution context, if any."""
    return _call.get()


def _current_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class _Continuation:
    def __init__(self) -> None:
        self.active = True
        self.started = False
        self.finished = False
        self.thread = threading.get_ident()
        self.task = _current_task()

    def start(self) -> None:
        if not self.active:
            raise FunctionCallError("Continuation is no longer active")
        if threading.get_ident() != self.thread or _current_task() is not self.task:
            raise FunctionCallError("Continuation must execute in its handler's task and thread")
        if self.started:
            raise FunctionCallError("Continuation may start only once")
        self.started = True

    def check_completion(self) -> None:
        if not self.finished:
            raise FunctionCallError("Handler returned without completing its continuation")


def _new_call(definition: FunctionDefinition, arguments: tuple[object, ...], keyword_name_to_value: dict[str, Any]) -> FunctionCall:
    parent = _call.get()
    return FunctionCall(generate_ulid(), parent.call_id if parent else None, definition, arguments, MappingProxyType(dict(keyword_name_to_value)))


def _invoke(definition: FunctionDefinition, arguments: tuple[object, ...], keyword_name_to_value: dict[str, Any]) -> object:
    handler = _handler.get()
    if handler is None:
        return definition.transformed_function(*arguments, **keyword_name_to_value)
    if inspect.isgeneratorfunction(definition.original_function) or inspect.isasyncgenfunction(definition.original_function):
        raise FunctionCallError("Intercepted generator functions are unsupported")
    call = _new_call(definition, arguments, keyword_name_to_value)
    guard = _Continuation()

    def proceed() -> object:
        guard.start()
        try:
            return definition.transformed_function(*arguments, **keyword_name_to_value)
        finally:
            guard.finished = True

    token = _call.set(call)
    try:
        result = handler.invoke(call, proceed)
        guard.check_completion()
        return result
    finally:
        guard.active = False
        _call.reset(token)


async def _invoke_async(
    definition: FunctionDefinition,
    body: Callable[..., Awaitable[object]],
    arguments: tuple[object, ...],
    keyword_name_to_value: dict[str, Any],
) -> object:
    handler = _handler.get()
    if handler is None:
        return await body(*arguments, **keyword_name_to_value)
    call = _new_call(definition, arguments, keyword_name_to_value)
    guard = _Continuation()
    continuations: list[Coroutine[Any, Any, object]] = []

    async def execute() -> object:
        guard.start()
        try:
            return await body(*arguments, **keyword_name_to_value)
        finally:
            guard.finished = True

    def proceed() -> Awaitable[object]:
        if not guard.active:
            raise FunctionCallError("Continuation is no longer active")
        coroutine = execute()
        continuations.append(coroutine)
        return coroutine

    token = _call.set(call)
    try:
        result = await handler.invoke_async(call, proceed)
        guard.check_completion()
        return result
    finally:
        guard.active = False
        for coroutine in continuations:
            if inspect.getcoroutinestate(coroutine) == inspect.CORO_CREATED:
                coroutine.close()
        _call.reset(token)
