from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

import nighthawk as nh
from nighthawk.runtime import StepCandidate, StepRequest

_MODULE_SOURCE = '''
import nighthawk as nh


@nh.function
def uses_later_helper() -> None:
    """execute
    Do nothing.
    """


@nh.function
async def uses_later_helper_async() -> None:
    """execute
    Do nothing.
    """


def later_helper() -> int:
    return 1
'''


class _GlobalsProbeExecutor:
    def __init__(self) -> None:
        self.later_helper_list: list[object] = []

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        self.later_helper_list.append(request.step_context.environment.python_name_to_value.get("later_helper"))
        return StepCandidate(binding_name_to_value={})


@pytest.fixture
def probe_module(tmp_path: Path) -> Iterator[ModuleType]:
    module_path = tmp_path / "nighthawk_globals_probe_module.py"
    module_path.write_text(_MODULE_SOURCE, encoding="utf-8")
    module_name = "nighthawk_globals_probe_module"
    specification = importlib.util.spec_from_file_location(module_name, module_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)


def test_natural_function_sees_module_names_defined_after_decoration(probe_module: ModuleType) -> None:
    probe_executor = _GlobalsProbeExecutor()

    with nh.run(probe_executor):
        probe_module.uses_later_helper()

    assert probe_executor.later_helper_list == [probe_module.later_helper]


def test_async_natural_function_sees_module_names_defined_after_decoration(probe_module: ModuleType) -> None:
    import asyncio

    probe_executor = _GlobalsProbeExecutor()

    with nh.run(probe_executor):
        asyncio.run(probe_module.uses_later_helper_async())

    assert probe_executor.later_helper_list[0] is probe_module.later_helper


def test_decoration_and_execution_preserve_module_globals() -> None:
    def workflow() -> None:
        """execute
        Do nothing.
        """

    module_name_to_value = workflow.__globals__
    original_name_to_value = dict(module_name_to_value)
    decorated = nh.function(workflow)
    definition = nh.get_function_definition(decorated)
    assert definition is not None
    transformed = definition.transformed_function
    assert transformed.__globals__ is module_name_to_value
    assert module_name_to_value.keys() == original_name_to_value.keys()
    assert all(module_name_to_value[name] is value for name, value in original_name_to_value.items())

    executor = _GlobalsProbeExecutor()
    with nh.run(executor):
        decorated()
    assert len(executor.later_helper_list) == 1
    assert module_name_to_value.keys() == original_name_to_value.keys()
    assert all(module_name_to_value[name] is value for name, value in original_name_to_value.items())
