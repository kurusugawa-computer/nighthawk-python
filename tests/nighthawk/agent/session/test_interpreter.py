"""Production cell compiler and source registry without provider construction."""

import inspect
import linecache
from typing import Any

import pytest

import nighthawk as nh
from nighthawk.agent import RunRejectedError
from nighthawk.agent.session._interpreter import _Interpreter
from nighthawk.agent.session._source import _SourceRegistry


async def test_await_final_expression_once_and_future_flags_persist() -> None:
    bindings: dict[str, Any] = {"nh": nh}
    sources = _SourceRegistry((), (), None)
    interpreter = _Interpreter("test", bindings, sources)
    results: list[object] = []
    try:
        await interpreter.execute("from __future__ import annotations\nvalues = []\nvalues.append(1) or len(values)", "first", results.append)
        assert results == [1]
        assert bindings["values"] == [1]
        assert "_" not in bindings
        await interpreter.execute("import asyncio\nawait asyncio.sleep(0)\nvalues.append(2)\nvalues", "second", results.append)
        assert bindings["values"] == [1, 2]
        await interpreter.execute("def forward() -> Later:\n    return 3\n", "third", results.append)
        with pytest.raises(RunRejectedError):
            sources.result_type(bindings["forward"])
        await interpreter.execute("Later = int", "fourth", results.append)
        assert sources.result_type(bindings["forward"]) is int
        assert not any(name.startswith("__nighthawk_") for name in bindings)
    finally:
        filenames = tuple(interpreter.filename_list)
        interpreter.dispose()
        sources.dispose()
    assert all(filename not in linecache.cache for filename in filenames)


async def test_natural_source_approval_and_definitions_survive_later_failure() -> None:
    bindings: dict[str, Any] = {"nh": nh}
    sources = _SourceRegistry((), (), None)
    interpreter = _Interpreter("test", bindings, sources)
    source = "@nh.function\ndef approved(value: int) -> int:\n    return value + 1\n"
    try:
        with pytest.raises(ValueError):
            await interpreter.execute(source + "raise ValueError('later')", "first", lambda value: None)
        function = bindings["approved"]
        assert sources.function_to_approval[function].source == source
        assert inspect.getsource(function) == source
        assert sources.result_type(function) is int
        assert function(6) == 7
        await interpreter.execute("def unannotated():\n    return 1", "second", lambda value: None)
        with pytest.raises(RunRejectedError):
            sources.result_type(bindings["unannotated"])

        def unrelated() -> int:
            return 99

        bindings["replace"] = lambda function: unrelated
        await interpreter.execute("@replace\ndef replaced() -> int:\n    return 1", "third", lambda value: None)
        assert bindings["replaced"] is unrelated
        assert unrelated not in sources.function_to_approval
    finally:
        interpreter.dispose()
        sources.dispose()


async def test_expression_coroutine_is_displayed_without_implicit_await() -> None:
    bindings: dict[str, Any] = {}
    interpreter = _Interpreter("test", bindings, _SourceRegistry((), (), None))
    results: list[Any] = []
    try:
        await interpreter.execute("called = []\nasync def work() -> int:\n    called.append(1)\n    return 7\nwork()", "first", results.append)
        assert inspect.iscoroutine(results[0])
        assert bindings["called"] == []
        results[0].close()
    finally:
        interpreter.dispose()
