from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepRequest, inspect_value

pytestmark = pytest.mark.skipif(sys.version_info < (3, 14), reason="CPython deferred annotations require Python 3.14")


def load_module(tmp_path: Path, source: str) -> ModuleType:
    path = tmp_path / "deferred_annotation_fixture.py"
    path.write_text(source)
    specification = importlib.util.spec_from_file_location("deferred_annotation_fixture", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_decorator_does_not_eagerly_evaluate_return_annotation(tmp_path: Path) -> None:
    module = load_module(
        tmp_path,
        '''import nighthawk as nh
effects = []
def contract():
    effects.append("annotation")
    return int
@nh.function
def workflow() -> contract():
    """execute
    Complete this step.
    """
    return 1
''',
    )
    assert module.effects == []

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            assert request.return_annotation is int
            assert module.effects == ["annotation"]
            return StepCandidate(outcome=Pass())

    with nh.run(Executor()):
        assert module.workflow() == 1
    assert module.effects == ["annotation"]


def test_deferred_global_output_contract_is_resolved_before_validation(tmp_path: Path) -> None:
    module = load_module(
        tmp_path,
        '''import nighthawk as nh
from typing import Annotated
from pydantic import Field
result: Annotated[int, Field(ge=0, le=10)] = 0
@nh.function
def workflow() -> int:
    global result
    """execute
    Update <:result>.
    """
    return result
''',
    )
    assert "__annotations__" not in vars(module)

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 11})

    with nh.run(Executor()), pytest.raises(nh.ExecutionError, match="less than or equal to 10"):
        module.workflow()
    assert module.result == 0


def test_source_metadata_does_not_call_custom_annotation_evaluator(tmp_path: Path) -> None:
    module = load_module(
        tmp_path,
        """effects = []
def action(value: int) -> str:
    return str(value)
def annotate(format):
    effects.append("annotation")
    raise AssertionError("must not run for presentation")
action.__annotate__ = annotate
""",
    )
    description = inspect_value(module.action)
    assert description["signature"] == "(value: int) -> str"
    assert "not evaluated" in str(description["annotation_note"])
    assert module.effects == []
    Path(module.action.__code__.co_filename).unlink()
    description = inspect_value(module.action)
    assert description["signature"] == "(value)"
    assert "source unavailable" in str(description["annotation_note"])
    assert module.effects == []


def test_unused_deferred_module_annotations_are_not_evaluated_for_presentation(tmp_path: Path) -> None:
    module = load_module(
        tmp_path,
        '''import nighthawk as nh
effects = []
def annotation_effect():
    effects.append("annotation")
    raise AssertionError("unused module annotation")
unused: annotation_effect() = None
@nh.function
def workflow() -> int:
    result: int = 0
    """execute
    Update <:result>.
    """
    return result
''',
    )

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            assert module.effects == []
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 2})

    with nh.run(Executor()):
        assert module.workflow() == 2
    assert module.effects == []
