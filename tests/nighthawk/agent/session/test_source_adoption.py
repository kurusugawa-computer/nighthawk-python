"""Milestone 0 definition observation, with no production approval registry.

The compiler inserts an inner observer before user decorators and an outer
observer after them. A core-owned receipt relates a Natural wrapper to that
exact observed original, independently of writable inspection metadata.
These are second-tier identity decisions, not an in-process Python sandbox.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import linecache
import weakref
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import nighthawk as nh
from nighthawk import get_function_definition
from nighthawk.runtime.step_request import StepCandidate
from nighthawk.testing import ScriptedExecutor


@dataclass(frozen=True)
class _IncludedSourceApproval:
    path: Path
    source: str
    approval_identity: str


@dataclass(frozen=True)
class _Approval:
    source: str
    source_identity: str
    cell_id: str
    route: str
    included_source_approval_tuple: tuple[_IncludedSourceApproval, ...] = ()


class _DefinitionExperiment:
    def __init__(self) -> None:
        self.binding_name_to_value: dict[str, Any] = {"nh": nh}
        self.approval_list: list[_Approval] = []
        self.function_to_approval: dict[Callable[..., object], _Approval] = {}
        self.materialized_function_list: list[object] = []
        self.filename_list: list[str] = []

    def submit(self, source: str, *, included_source_approval_tuple: tuple[_IncludedSourceApproval, ...] = ()) -> None:
        for approval in included_source_approval_tuple:
            if approval.path.read_text() != approval.source:
                raise ValueError("included source differs from its approved text")
        filename = f"<milestone-zero-cell-{uuid4()}>"
        self.filename_list.append(filename)
        linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
        module = ast.parse(source, filename=filename)
        for definition in module.body:
            if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            first_line = min([definition.lineno, *(decorator.lineno for decorator in definition.decorator_list)])
            definition_source = "".join(source.splitlines(keepends=True)[first_line - 1 : definition.end_lineno])
            approval = _Approval(
                definition_source,
                hashlib.sha256(definition_source.encode()).hexdigest(),
                filename,
                "operator_submission",
                included_source_approval_tuple,
            )
            self.approval_list.append(approval)
            original_list: list[Callable[..., object]] = []

            def observe_original(function: Callable[..., object], observed: list[Callable[..., object]] = original_list) -> Callable[..., object]:
                observed.append(function)
                return function

            def observe_result(
                function: object,
                observed: list[Callable[..., object]] = original_list,
                evidence: _Approval = approval,
            ) -> object:
                self.materialized_function_list.append(function)
                definition = get_function_definition(function) if callable(function) else None
                if callable(function) and (function is observed[0] or (definition is not None and definition.original_function is observed[0])):
                    self.function_to_approval[function] = evidence
                return function

            original_name = f"__milestone_zero_original_{len(self.approval_list)}"
            result_name = f"__milestone_zero_result_{len(self.approval_list)}"
            self.binding_name_to_value[original_name] = observe_original
            self.binding_name_to_value[result_name] = observe_result
            location = definition.decorator_list[0] if definition.decorator_list else definition
            definition.decorator_list.insert(0, ast.copy_location(ast.Name(id=result_name, ctx=ast.Load()), location))
            definition.decorator_list.append(ast.copy_location(ast.Name(id=original_name, ctx=ast.Load()), location))
        ast.fix_missing_locations(module)
        exec(compile(module, filename, "exec"), self.binding_name_to_value)  # noqa: S102 - Execute trusted test source to verify definition evidence.

    def register_imported(self, function: Callable[..., object], *, source: str, approval_identity: str) -> None:
        self.function_to_approval[function] = _Approval(
            source,
            hashlib.sha256(source.encode()).hexdigest(),
            approval_identity,
            "trusted_import_registration",
        )

    def close(self) -> None:
        self.function_to_approval.clear()
        for filename in self.filename_list:
            linecache.cache.pop(filename, None)


@pytest.fixture
def experiment() -> Iterator[_DefinitionExperiment]:
    value = _DefinitionExperiment()
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize("decoration", ["", "@nh.function\n"])
def test_observed_definition_preserves_exact_source_aliases_and_redefinition(experiment: _DefinitionExperiment, decoration: str) -> None:
    first_source = decoration + "def increment(value: int) -> int:\n    return value + 1\n"
    experiment.submit(first_source)
    first = experiment.binding_name_to_value["increment"]
    experiment.submit("alias = increment\n" + decoration + "def increment(value: int) -> int:\n    return value + 2\n")
    second = experiment.binding_name_to_value["increment"]
    assert experiment.function_to_approval[first].source == first_source
    assert experiment.function_to_approval[first].source_identity != experiment.function_to_approval[second].source_identity
    assert experiment.binding_name_to_value["alias"] is first
    assert first(6) == 7 and second(6) == 8
    assert inspect.getsource(first) == first_source
    experiment.submit("del increment\n")
    assert "increment" not in experiment.binding_name_to_value
    assert experiment.binding_name_to_value["alias"] in experiment.function_to_approval


def test_successful_definition_survives_later_cell_failure(experiment: _DefinitionExperiment) -> None:
    with pytest.raises(ValueError, match="later"):
        experiment.submit("def retained() -> int:\n    return 7\nraise ValueError('later')\n")
    function = experiment.binding_name_to_value["retained"]
    assert function in experiment.function_to_approval
    assert function() == 7
    assert len(experiment.materialized_function_list) == 1


def test_decorator_failure_records_approval_without_materialization(experiment: _DefinitionExperiment) -> None:
    def fail(function: object) -> object:
        raise ValueError("decoration failed")

    experiment.binding_name_to_value["fail"] = fail
    with pytest.raises(ValueError, match="decoration failed"):
        experiment.submit("@fail\ndef missing() -> int:\n    return 7\n")
    assert len(experiment.approval_list) == 1
    assert experiment.materialized_function_list == []
    assert experiment.function_to_approval == {}
    assert "missing" not in experiment.binding_name_to_value


def test_unrelated_decorator_result_does_not_receive_original_approval(experiment: _DefinitionExperiment) -> None:
    def unrelated() -> int:
        return 99

    experiment.binding_name_to_value["replace"] = lambda function: unrelated
    experiment.submit("@replace\ndef candidate() -> int:\n    return 7\n")
    assert experiment.binding_name_to_value["candidate"] is unrelated
    assert experiment.materialized_function_list == [unrelated]
    assert unrelated not in experiment.function_to_approval


def test_noncallable_decorator_result_remains_an_ordinary_operator_value(experiment: _DefinitionExperiment) -> None:
    experiment.binding_name_to_value["replace"] = lambda function: [7]
    experiment.submit("@replace\ndef candidate() -> int:\n    return 7\n")
    assert experiment.binding_name_to_value["candidate"] == [7]
    assert experiment.function_to_approval == {}


def test_natural_definition_has_a_core_owned_receipt_for_the_observed_original(experiment: _DefinitionExperiment) -> None:
    source = '''@nh.function
def describe(value: int) -> int:
    result: int = 0
    """execute
    Set <:result> from <value>.
    """
    return result
'''
    experiment.submit(source)
    function = experiment.binding_name_to_value["describe"]
    assert experiment.approval_list[0].source == source
    assert experiment.materialized_function_list == [function]
    assert experiment.function_to_approval[function].source == source
    definition = get_function_definition(function)
    assert definition is not None
    assert definition.transformed_function.__globals__ is experiment.binding_name_to_value
    with nh.run(ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": 7})])):
        assert function(6) == 7


def test_public_metadata_cannot_forge_core_transformation_evidence(experiment: _DefinitionExperiment) -> None:
    experiment.submit("@nh.function\ndef original() -> int:\n    return 7\n")
    original = experiment.binding_name_to_value["original"]
    definition = get_function_definition(original)
    assert definition is not None

    def unrelated() -> int:
        return 99

    unrelated.__wrapped__ = original.__wrapped__  # type: ignore[attr-defined]
    unrelated.__nighthawk_transformed_function__ = definition.transformed_function  # type: ignore[attr-defined]
    assert inspect.getsource(unrelated) == inspect.getsource(original)
    assert unrelated.__nighthawk_transformed_function__ is definition.transformed_function  # type: ignore[attr-defined]
    assert get_function_definition(unrelated) is None
    assert get_function_definition(original) is not None
    assert unrelated() == 99
    assert unrelated not in experiment.function_to_approval


def _reviewed_import(value: int) -> int:
    return value + 1


def test_import_registration_is_an_explicit_assertion_and_close_expires_it(experiment: _DefinitionExperiment) -> None:
    source = "def _reviewed_import(value: int) -> int:\n    return value + 1\n"
    assert _reviewed_import not in experiment.function_to_approval
    experiment.register_imported(_reviewed_import, source=source, approval_identity="repository-review-milestone-zero")
    approval = experiment.function_to_approval[_reviewed_import]
    assert approval.source == source
    assert approval.route == "trusted_import_registration"
    experiment.close()
    assert _reviewed_import not in experiment.function_to_approval
    assert _reviewed_import(6) == 7


def test_included_text_is_approved_before_core_fstring_preprocessing(experiment: _DefinitionExperiment, tmp_path: Path) -> None:
    path = tmp_path / "instructions.md"
    included_text = "Use the approved description."
    path.write_text(included_text)
    included_approval = _IncludedSourceApproval(path.resolve(), included_text, "reviewed-instructions")
    source = '''@nh.function
def included() -> int:
    result: int = 0
    f"""execute
    {included_text}
    Set <:result> to 7.
    """
    return result
'''
    experiment.binding_name_to_value["included_text"] = included_approval.source
    path.write_text("Unapproved replacement.")
    with pytest.raises(ValueError, match="included source"):
        experiment.submit(source, included_source_approval_tuple=(included_approval,))
    assert experiment.materialized_function_list == []
    assert "included" not in experiment.binding_name_to_value
    path.write_text(included_text)
    experiment.submit(source, included_source_approval_tuple=(included_approval,))
    function = experiment.binding_name_to_value["included"]
    assert experiment.function_to_approval[function].included_source_approval_tuple == (included_approval,)
    path.write_text("A later file change must not replace the approved snapshot.")
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"result": 7})])
    with nh.run(executor):
        assert function() == 7
    assert included_text in executor.requests[0].processed_natural_program
    assert "later file change" not in executor.requests[0].processed_natural_program


def test_receipt_registry_does_not_retain_a_closed_definition_namespace(experiment: _DefinitionExperiment) -> None:
    import gc

    experiment.submit("@nh.function\ndef disposable() -> int:\n    return 7\n")
    function_reference = weakref.ref(experiment.binding_name_to_value["disposable"])
    experiment.close()
    experiment.materialized_function_list.clear()
    experiment.binding_name_to_value.clear()
    gc.collect()
    assert function_reference() is None
