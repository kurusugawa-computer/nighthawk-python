from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

import nighthawk as nh
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_contract import PassStepOutcome, StepKind, StepOutcome

_MODULE_SOURCE = '''
import nighthawk as nh


@nh.natural_function
def uses_later_helper() -> None:
    """natural
    Do nothing.
    """


@nh.natural_function
async def uses_later_helper_async() -> None:
    """natural
    Do nothing.
    """


def later_helper() -> int:
    return 1
'''


class _GlobalsProbeExecutor:
    def __init__(self) -> None:
        self.step_globals_list: list[dict[str, object]] = []

    def run_step(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[StepKind, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        _ = (processed_natural_program, binding_names, allowed_step_kinds)
        self.step_globals_list.append(step_context.step_globals)
        return PassStepOutcome(kind="pass"), {}


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

    assert len(probe_executor.step_globals_list) == 1
    assert probe_executor.step_globals_list[0]["later_helper"] is probe_module.later_helper


def test_async_natural_function_sees_module_names_defined_after_decoration(probe_module: ModuleType) -> None:
    import asyncio

    probe_executor = _GlobalsProbeExecutor()

    with nh.run(probe_executor):
        asyncio.run(probe_module.uses_later_helper_async())

    assert probe_executor.step_globals_list[0]["later_helper"] is probe_module.later_helper


def test_transformed_function_shares_module_globals_without_pollution(probe_module: ModuleType) -> None:
    transformed = probe_module.uses_later_helper.__wrapped__
    assert transformed is not probe_module.uses_later_helper
    assert "__nighthawk_runner__" in transformed.__code__.co_freevars
    assert transformed.__globals__ is probe_module.__dict__
    assert "__nh_factory__" not in probe_module.__dict__
    assert "__nighthawk_runner__" not in probe_module.__dict__
    assert "__nh_extract_program__" not in probe_module.__dict__
    assert "__nh_python_cell_scope__" not in probe_module.__dict__
