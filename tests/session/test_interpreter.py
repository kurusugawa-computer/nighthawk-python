import gc
import inspect
import linecache
import weakref
from typing import Any

import pytest

import nighthawk as nh
from nighthawk.core.natural.definition import get_function_definition
from nighthawk.core.step_request import StepCandidate
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.errors import RunRejectedError
from nighthawk.session._interpreter import _Interpreter
from nighthawk.session._source import _SourceRegistry


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


@pytest.mark.parametrize("decoration", ["", "@nh.function\n"])
async def test_redefinition_receives_a_new_approval_while_aliases_keep_the_original(decoration: str) -> None:
    bindings: dict[str, Any] = {"nh": nh}
    sources = _SourceRegistry((), (), None)
    interpreter = _Interpreter("test", bindings, sources)
    first_source = decoration + "def increment(value: int) -> int:\n    return value + 1\n"
    try:
        await interpreter.execute(first_source, "first", lambda value: None)
        first = bindings["increment"]
        await interpreter.execute(
            "alias = increment\n" + decoration + "def increment(value: int) -> int:\n    return value + 2\n", "second", lambda value: None
        )
        second = bindings["increment"]
        first_approval, second_approval = sources.approval_for(first), sources.approval_for(second)
        assert first_approval is not None and second_approval is not None
        assert first_approval.source == first_source
        assert first_approval.source_identity != second_approval.source_identity
        assert bindings["alias"] is first
        assert (first(6), second(6)) == (7, 8)
        await interpreter.execute("del increment\n", "third", lambda value: None)
        assert "increment" not in bindings
        assert sources.approval_for(bindings["alias"]) is first_approval
    finally:
        interpreter.dispose()
        sources.dispose()


async def test_failed_or_noncallable_decoration_records_no_approval() -> None:
    def fail(function: object) -> object:
        raise ValueError("decoration failed")

    bindings: dict[str, Any] = {"nh": nh, "fail": fail, "listify": lambda function: [7]}
    sources = _SourceRegistry((), (), None)
    interpreter = _Interpreter("test", bindings, sources)
    try:
        with pytest.raises(ValueError, match="decoration failed"):
            await interpreter.execute("@fail\ndef missing() -> int:\n    return 7\n", "first", lambda value: None)
        assert "missing" not in bindings
        await interpreter.execute("@listify\ndef candidate() -> int:\n    return 7\n", "second", lambda value: None)
        assert bindings["candidate"] == [7]
        assert sources.function_to_approval == {}
    finally:
        interpreter.dispose()
        sources.dispose()


async def test_copied_wrapper_metadata_does_not_transfer_definition_or_approval() -> None:
    bindings: dict[str, Any] = {"nh": nh}
    sources = _SourceRegistry((), (), None)
    interpreter = _Interpreter("test", bindings, sources)
    try:
        await interpreter.execute("@nh.function\ndef original() -> int:\n    return 7\n", "first", lambda value: None)
        original = bindings["original"]
        definition = get_function_definition(original)
        assert definition is not None

        def unrelated() -> int:
            return 99

        unrelated.__wrapped__ = original.__wrapped__  # type: ignore[attr-defined]
        unrelated.__nighthawk_transformed_function__ = definition.transformed_function  # type: ignore[attr-defined]
        assert inspect.getsource(unrelated) == inspect.getsource(original)
        assert get_function_definition(unrelated) is None
        assert sources.approval_for(unrelated) is None
        assert sources.approval_for(original) is not None
    finally:
        interpreter.dispose()
        sources.dispose()


async def test_natural_definition_uses_the_cell_namespace_and_disposal_releases_it() -> None:
    bindings: dict[str, Any] = {"nh": nh}
    sources = _SourceRegistry((), (), None)
    interpreter = _Interpreter("test", bindings, sources)
    source = '''@nh.function
def describe(value: int) -> int:
    result: int = 0
    """execute
    Set <:result> from <value>.
    """
    return result
'''
    await interpreter.execute(source, "first", lambda value: None)
    function = bindings["describe"]
    assert sources.function_to_approval[function].source == source
    definition = get_function_definition(function)
    assert definition is not None
    assert definition.transformed_function.__globals__ is bindings
    with nh.run(ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": 7})])):
        assert function(6) == 7
    reference = weakref.ref(function)
    del function, definition
    interpreter.dispose()
    sources.dispose()
    assert sources.function_to_approval == {}
    bindings.clear()
    gc.collect()
    assert reference() is None
