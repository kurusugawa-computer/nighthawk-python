from typing import Annotated

import pytest
from pydantic import Field

from nighthawk.harness.context import current_capabilities
from nighthawk.harness.dispatch import dispatch
from nighthawk.harness.errors import CapabilityRejectedError, DispatchRejectedError
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import CapabilityInvocationEvent, DispatchEvent
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.run_handle import RunHandle
from tests.support.harness import make_runner
from tests.support.runs import EchoInput, EchoResult, run_body


class Document:
    pass


def test_annotated_constraints_are_preserved_at_both_boundaries() -> None:
    from nighthawk.harness._call_contracts import bind_and_validate_arguments, build_call_contract, validate_result

    def positive(value: Annotated[int, Field(gt=0)]) -> Annotated[int, Field(gt=0)]:
        return value

    contract = build_call_contract(positive)
    assert bind_and_validate_arguments(contract, ("3",), {}) == ((3,), {})
    assert validate_result(contract, "3") == 3
    with pytest.raises(ValueError, match="argument 'value'"):
        bind_and_validate_arguments(contract, (0,), {})
    with pytest.raises(ValueError, match="result"):
        validate_result(contract, 0)


@pytest.mark.parametrize("generated", [False, True])
def test_application_class_contracts_preserve_identity_and_refuse_other_types(generated: bool) -> None:
    from nighthawk.harness.errors import GeneratedExecutionError
    from nighthawk.harness.proposals import DependencyDeclaration

    observed: list[object] = []
    document = Document()

    def accept(value: Document) -> Document:
        observed.append(value)
        return value

    def wrong_result() -> Document:
        return 123  # type: ignore[return-value]

    def body(value: EchoInput) -> EchoResult:
        if generated:
            exports = dispatch(
                CodeProposal(
                    source="def accept(value: Document) -> Document:\n    return value\ndef wrong_result() -> Document:\n    return 123\n",
                    export_name_tuple=("accept", "wrong_result"),
                    dependency_tuple=(DependencyDeclaration(name="Document", kind="reference", declared_type=type),),
                )
            ).export_name_to_value
        else:
            exports = current_capabilities()
        function, invalid = exports["accept"], exports["wrong_result"]
        assert callable(function) and callable(invalid)
        assert function(document) is document
        with pytest.raises(DispatchRejectedError if generated else CapabilityRejectedError):
            function(123)
        with pytest.raises(GeneratedExecutionError if generated else CapabilityRejectedError):
            invalid()
        return EchoResult()

    run_body(
        body,
        runner=make_runner(),
        run_entry_grant=Grant.of(
            Capability(name="accept", function=accept, authority_boundary="retain a document"),
            Capability(name="wrong_result", function=wrong_result, authority_boundary="invalid document result"),
            Document=Document,
        ),
    )
    assert observed == ([] if generated else [document])


def test_unresolved_annotations_are_not_silently_dropped_or_resolved_in_the_harness() -> None:
    from nighthawk.harness._call_contracts import build_call_contract

    # CallContract exists in the Harness module, but not in this function's namespace.
    def unresolved(value: "CallContract") -> "CallContract":  # type: ignore[name-defined] # noqa: F821
        return value

    with pytest.raises(ValueError, match="defining namespace"):
        build_call_contract(unresolved)


@pytest.mark.parametrize("future_annotations", [False, True])
def test_generated_value_exports_use_the_same_application_class_contracts(future_annotations: bool) -> None:
    from nighthawk.harness.errors import GeneratedExecutionError
    from nighthawk.harness.proposals import DependencyDeclaration

    document = Document()

    def body(run_input: EchoInput) -> EchoResult:
        result = dispatch(
            CodeProposal(
                source=("from __future__ import annotations\n" if future_annotations else "") + "observed: Document = document\n",
                export_name_tuple=("observed",),
                dependency_tuple=(
                    DependencyDeclaration(name="Document", kind="reference", declared_type=type),
                    DependencyDeclaration(name="document", kind="reference", declared_type=Document),
                ),
            )
        )
        assert result.export_name_to_value["observed"] is document
        with pytest.raises(GeneratedExecutionError, match="does not satisfy"):
            dispatch(
                CodeProposal(
                    source="observed: Document = 7\n",
                    export_name_tuple=("observed",),
                    dependency_tuple=(DependencyDeclaration(name="Document", kind="reference", declared_type=type),),
                )
            )
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(Document=Document, document=document))


def test_callable_instances_and_partial_functions_resolve_their_own_annotations() -> None:
    from functools import partial

    from nighthawk.harness._call_contracts import bind_and_validate_arguments, build_call_contract, validate_result

    class Receiver:
        def __call__(self, value: "Document") -> "Document":
            return value

    receiver = Receiver()
    document = Document()
    for function in (receiver, partial(receiver)):
        contract = build_call_contract(function)
        assert bind_and_validate_arguments(contract, (document,), {})[0][0] is document
        assert validate_result(contract, document) is document
        with pytest.raises(ValueError, match="argument 'value'"):
            bind_and_validate_arguments(contract, (123,), {})


def test_explicit_none_annotation_is_a_contract() -> None:
    from nighthawk.harness._call_contracts import bind_and_validate_arguments, build_call_contract

    def accepts_none(value: None) -> None:
        return value

    contract = build_call_contract(accepts_none)
    assert bind_and_validate_arguments(contract, (None,), {}) == ((None,), {})
    with pytest.raises(ValueError, match="argument 'value'"):
        bind_and_validate_arguments(contract, (123,), {})


def _total(base: int = 1, *values: int, scale: int = 2, **adjustments: int) -> int:
    return (base + sum(values) + sum(adjustments.values())) * scale


@pytest.mark.parametrize("generated", [False, True])
def test_defaults_and_variadic_items_cross_the_same_typed_contract(generated: bool) -> None:
    handle = RunHandle()
    total = Capability(name="total", function=_total, authority_boundary="arithmetic")

    def body(value: EchoInput) -> EchoResult:
        if generated:
            function = dispatch(
                CodeProposal(
                    source="def total(base: int = 1, *values: int, scale: int = 2, **adjustments: int) -> int:\n"
                    "    return (base + sum(values) + sum(adjustments.values())) * scale\n",
                    export_name_tuple=("total",),
                )
            ).export_name_to_value["total"]
        else:
            function = current_capabilities()["total"]
        assert callable(function)
        assert function() == 2
        assert function("3", "4", scale="5", bonus="6") == 65
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(total), handle=handle)
    argument_tuple = tuple(
        event.argument_snapshot
        for entry in handle.ledger.entries
        if isinstance(event := entry.event, CapabilityInvocationEvent) or isinstance(event, DispatchEvent) and event.dispatch_kind == "invocation"
    )
    assert argument_tuple == (
        {"positional": (1,), "keyword": {"scale": 2}},
        {"positional": (3, 4), "keyword": {"scale": 5, "bonus": 6}},
    )


@pytest.mark.parametrize("generated", [False, True])
@pytest.mark.parametrize("invalid_argument", ["positional", "keyword"])
def test_invalid_variadic_items_are_refused_before_the_body(generated: bool, invalid_argument: str) -> None:
    observed: list[str] = []

    def act(*values: int, **adjustments: int) -> None:
        observed.append("entered")

    capability = Capability(name="act", function=act, authority_boundary="record entry")

    def body(value: EchoInput) -> EchoResult:
        if generated:
            selected = dispatch(
                CodeProposal(
                    source="def act(*values: int, **adjustments: int) -> None:\n    raise AssertionError('body entered')\n",
                    export_name_tuple=("act",),
                )
            ).export_name_to_value["act"]
        else:
            selected = current_capabilities()["act"]
        assert callable(selected)
        with pytest.raises(DispatchRejectedError if generated else CapabilityRejectedError):
            if invalid_argument == "positional":
                selected("invalid")
            else:
                selected(adjustment="invalid")
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(capability))
    assert observed == []


def test_mutable_defaults_keep_python_identity_while_evidence_captures_each_call() -> None:
    retained: list[int] = []
    handle = RunHandle()

    def append(value: int, values: list[int] = retained) -> int:
        assert values is retained
        values.append(value)
        return len(values)

    capability = Capability(name="append", function=append, authority_boundary="append to retained defaults")

    def body(value: EchoInput) -> EchoResult:
        function = current_capabilities()["append"]
        assert callable(function)
        assert function(1) == 1
        assert function(2) == 2
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=Grant.of(capability), handle=handle)
    assert retained == [1, 2]
    assert tuple(entry.event.argument_snapshot for entry in handle.ledger.entries if isinstance(entry.event, CapabilityInvocationEvent)) == (
        {"positional": (1, ()), "keyword": {}},
        {"positional": (2, (1,)), "keyword": {}},
    )
