"""Host proxies that keep suspended generated code inside its dispatch.

A generator, async generator, or coroutine created by generated code resumes
through ``send``, ``throw``, ``close`` (or their asynchronous counterparts),
including the exceptional resumptions ``close()`` and garbage collection cause.
Generated source is compiled so that every generator or coroutine function
returns one of these proxies around the raw object; each resumption first asks
the host whether the current context is a live generated execution of the
defining source, so a leaked object can neither continue nor run its handlers
outside its dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Coroutine, Generator
from types import TracebackType
from typing import Any

__all__ = [
    "GUARD_NAME",
    "WRAP_ASYNC_GENERATOR_NAME",
    "WRAP_COROUTINE_NAME",
    "WRAP_GENERATOR_NAME",
    "GuardedAsyncGenerator",
    "GuardedCoroutine",
    "GuardedGenerator",
    "guard_name_to_value",
]

GUARD_NAME = "__nighthawk_guard__"
"""Namespace binding, supplied by the host, that every generated function body and handler calls before it runs."""

WRAP_GENERATOR_NAME = "__nighthawk_wrap_generator__"
WRAP_ASYNC_GENERATOR_NAME = "__nighthawk_wrap_async_generator__"
WRAP_COROUTINE_NAME = "__nighthawk_wrap_coroutine__"

type Guard = Callable[[], None]


class GuardedGenerator(Generator[Any, Any, Any]):
    """Generator proxy whose every resumption is admitted by the host guard."""

    def __init__(self, generator: Generator[Any, Any, Any], guard: Guard) -> None:
        self._generator = generator
        self._guard = guard

    def send(self, value: Any) -> Any:
        self._guard()
        return self._generator.send(value)

    def throw(self, typ: Any, val: Any = None, tb: TracebackType | None = None) -> Any:  # type: ignore[override]
        self._guard()
        if val is None and tb is None:
            return self._generator.throw(typ)
        return self._generator.throw(typ, val, tb)

    def close(self) -> None:
        self._guard()
        self._generator.close()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._generator!r})"


class GuardedAsyncGenerator(AsyncGenerator[Any, Any]):
    """Async generator proxy whose every resumption is admitted by the host guard."""

    def __init__(self, generator: AsyncGenerator[Any, Any], guard: Guard) -> None:
        self._generator = generator
        self._guard = guard

    async def asend(self, value: Any) -> Any:
        self._guard()
        return await self._generator.asend(value)

    async def athrow(self, typ: Any, val: Any = None, tb: TracebackType | None = None) -> Any:  # type: ignore[override]
        self._guard()
        if val is None and tb is None:
            return await self._generator.athrow(typ)
        return await self._generator.athrow(typ, val, tb)

    async def aclose(self) -> None:
        self._guard()
        await self._generator.aclose()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._generator!r})"


class GuardedCoroutine(Coroutine[Any, Any, Any]):
    """Coroutine proxy whose every resumption is admitted by the host guard.

    It is a ``collections.abc.Coroutine``, so ``await`` and ``asyncio.run``
    accept it; awaiting it drives the raw coroutine through a guarded iterator.
    """

    def __init__(self, coroutine: Coroutine[Any, Any, Any], guard: Guard) -> None:
        self._coroutine = coroutine
        self._guard = guard

    def send(self, value: Any) -> Any:
        self._guard()
        return self._coroutine.send(value)

    def throw(self, typ: Any, val: Any = None, tb: TracebackType | None = None) -> Any:  # type: ignore[override]
        self._guard()
        if val is None and tb is None:
            return self._coroutine.throw(typ)
        return self._coroutine.throw(typ, val, tb)

    def close(self) -> None:
        self._guard()
        self._coroutine.close()

    def __await__(self) -> Generator[Any, Any, Any]:
        return GuardedGenerator(self._coroutine.__await__(), self._guard)  # type: ignore[arg-type]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._coroutine!r})"


def guard_name_to_value(guard: Guard) -> dict[str, object]:
    """Return the reserved namespace bindings generated source is compiled against."""

    def wrap_generator(generator: Generator[Any, Any, Any]) -> GuardedGenerator:
        return GuardedGenerator(generator, guard)

    def wrap_async_generator(generator: AsyncGenerator[Any, Any]) -> GuardedAsyncGenerator:
        return GuardedAsyncGenerator(generator, guard)

    def wrap_coroutine(coroutine: Coroutine[Any, Any, Any]) -> GuardedCoroutine:
        return GuardedCoroutine(coroutine, guard)

    return {
        GUARD_NAME: guard,
        WRAP_GENERATOR_NAME: wrap_generator,
        WRAP_ASYNC_GENERATOR_NAME: wrap_async_generator,
        WRAP_COROUTINE_NAME: wrap_coroutine,
    }
