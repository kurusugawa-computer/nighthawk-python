"""Run entry and run end are the two host seams of an agent run."""

from __future__ import annotations

import contextvars
import threading
from dataclasses import replace
from typing import cast

import pytest

import nighthawk as nh
from nighthawk.agent import (
    Capability,
    CodeProposal,
    DefinitionMaterializationEvent,
    DependencyDeclaration,
    DispatchEvent,
    DispatchOutcomeEvent,
    DispatchRejectedError,
    EffectFootprint,
    GeneratedCallableReference,
    Grant,
    GrantError,
    NaturalExecutionError,
    PerimeterPolicy,
    RunEndEvent,
    RunEntryEvent,
    RunFailedError,
    RunHandle,
    RunRejectedError,
    RunTerminationError,
    TerminationCompletionEvent,
    TraceAnomalyEvent,
    dispatch,
    scope,
)
from nighthawk.agent.runtime import Runner
from nighthawk.runtime.step_request import StepCandidate
from nighthawk.testing import ScriptedExecutor

from .conftest import ScriptedTermination, make_runner
from .support import EchoInput, EchoResult, event_kinds, run_body


def _greet(name: str) -> str:
    """Greet someone."""

    return f"hello {name}"


@nh.function
def _judge(text: str) -> str:
    label: str = ""
    """execute
    Set <:label> from <text>.
    """
    return label


def test_accepted_run_records_entry_definitions_and_end(runner: Runner) -> None:
    greet = Capability(name="greet", function=_greet, authority_boundary="pure formatting")
    handle = RunHandle()
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"label": "fine"})])

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=_judge(run_input.text))

    result = run_body(
        body,
        runner=runner,
        step_executor=executor,
        run_entry_grant=Grant.of(greet, seed=3),
        function_definitions=(_judge,),
        handle=handle,
    )
    assert result == EchoResult(value="fine")
    entries = handle.ledger.entries
    assert event_kinds(entries) == ["run_entry", "definition_materialization", "natural_execution_outcome", "run_end"]
    run_entry = entries[0].event
    assert isinstance(run_entry, RunEntryEvent)
    assert run_entry.input_type_name.endswith("EchoInput")
    assert run_entry.result_type_name.endswith("EchoResult")
    assert [descriptor.name for descriptor in run_entry.capability_descriptor_tuple] == ["greet"]
    assert run_entry.capability_descriptor_tuple[0].declared_type == "(name: str) -> str"
    assert run_entry.reference_name_tuple == ("seed",)
    assert run_entry.effective_perimeter_policy == PerimeterPolicy.permitting_all()
    assert run_entry.resource_allocation_identity.startswith("allocation-")
    definition = entries[1].event
    assert isinstance(definition, DefinitionMaterializationEvent)
    assert definition.source_provenance_class == "ratified"
    assert definition.export_name == "_judge"
    assert entries[1].parent_event_id == entries[0].event_id
    run_end = entries[-1].event
    assert isinstance(run_end, RunEndEvent)
    assert run_end.terminal_kind == "accepted"
    assert handle.run_id == handle.ledger.run_id


def test_input_outside_the_run_contract_is_rejected_before_the_run_starts(runner: Runner) -> None:
    handle = RunHandle()
    with pytest.raises(RunRejectedError) as captured:
        run_body(lambda run_input: EchoResult(), runner=runner, handle=handle, run_input="not an input")  # type: ignore[arg-type]
    assert captured.value.reason == "input_type_invalid"
    with pytest.raises(RuntimeError):
        _ = handle.ledger


def test_composition_perimeter_policy_may_only_narrow_the_platform_policy() -> None:
    runner = make_runner(perimeter_policy=PerimeterPolicy.permitting("egress"))
    with pytest.raises(RunRejectedError) as captured:
        run_body(lambda run_input: EchoResult(), runner=runner, composition_perimeter_policy=PerimeterPolicy.permitting("egress", "destructive"))
    assert captured.value.reason == "perimeter_policy_widening"
    assert captured.value.detail == "destructive"


def test_capability_outside_the_effective_perimeter_policy_is_rejected_at_entry(runner: Runner) -> None:
    writer = Capability(
        name="write",
        function=_greet,
        authority_boundary="writes",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"durable_write_back"})),
    )
    with pytest.raises(RunRejectedError) as captured:
        run_body(
            lambda run_input: EchoResult(),
            runner=runner,
            run_entry_grant=Grant.of(writer),
            composition_perimeter_policy=PerimeterPolicy.permitting("egress"),
        )
    assert captured.value.reason == "capability_outside_perimeter_policy"
    assert captured.value.detail == "write"


def test_result_outside_the_run_contract_is_a_typed_run_failure(runner: Runner) -> None:
    handle = RunHandle()
    with pytest.raises(RunFailedError) as captured:
        run_body(lambda run_input: "not a result", runner=runner, handle=handle)  # type: ignore[arg-type, return-value]
    assert captured.value.terminal_kind == "validation_rejection"
    run_end = handle.ledger.find(captured.value.terminal_event_id)
    assert run_end is not None and isinstance(run_end.event, RunEndEvent)
    assert run_end.event.terminal_kind == "validation_rejection"
    assert captured.value.run_id == handle.ledger.run_id


def test_uncaught_exception_is_a_typed_run_failure_chained_to_the_cause(runner: Runner) -> None:
    handle = RunHandle()

    def body(run_input: EchoInput) -> EchoResult:
        raise KeyError("boom")

    with pytest.raises(RunFailedError) as captured:
        run_body(body, runner=runner, handle=handle)
    assert captured.value.terminal_kind == "uncaught_exception"
    assert isinstance(captured.value.__cause__, KeyError)
    run_end = handle.ledger.entries[-1].event
    assert isinstance(run_end, RunEndEvent)
    assert run_end.failure_class_name == "KeyError"


def test_natural_execution_failure_propagates_as_ordinary_python_and_may_be_handled(runner: Runner) -> None:
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"label": object()})])

    def body(run_input: EchoInput) -> EchoResult:
        try:
            _judge(run_input.text)
        except NaturalExecutionError as failure:
            return EchoResult(value=failure.failure_kind)
        return EchoResult(value="unexpected")

    assert run_body(body, runner=runner, step_executor=executor) == EchoResult(value="write_set_invalid")


def test_host_operations_require_an_active_run() -> None:
    with pytest.raises(DispatchRejectedError) as dispatch_rejection:
        dispatch(CodeProposal(source="def f(): pass", export_name_tuple=("f",)))
    assert dispatch_rejection.value.reason == "no_active_run"
    with pytest.raises(GrantError) as grant_error, scope(Grant.EMPTY):
        pass
    assert grant_error.value.reason == "no_active_run"


def test_handle_termination_ends_the_run_at_the_next_seam_even_if_the_body_returns(runner: Runner) -> None:
    handle = RunHandle()
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"label": "a"}), StepCandidate(binding_name_to_value={"label": "b"})])
    observed: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        observed.append(_judge(run_input.text))
        handle.terminate("external policy")
        try:
            observed.append(_judge(run_input.text))
        except NaturalExecutionError as failure:
            observed.append(failure.failure_kind)
        return EchoResult(value="survived")

    with pytest.raises(RunFailedError) as captured:
        run_body(body, runner=runner, step_executor=executor, handle=handle)
    assert captured.value.terminal_kind == "host_termination"
    assert observed == ["a", "terminated"]
    kinds = event_kinds(handle.ledger.entries)
    assert kinds.count("run_end") == 1
    assert "termination_request" in kinds and "termination_completion" in kinds


def test_interior_branch_active_at_run_end_is_terminated_by_the_host_and_fails_the_run() -> None:
    termination = ScriptedTermination(ceased=True)
    runner = make_runner(termination=termination)
    handle = RunHandle()
    release = threading.Event()
    started = threading.Event()
    thread_outcome: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        result = dispatch(
            CodeProposal(
                source="def block() -> int:\n    started.set()\n    release.wait()\n    return 1\n",
                export_name_tuple=("block",),
                dependency_tuple=(
                    DependencyDeclaration(name="started", kind="reference", declared_type=threading.Event),
                    DependencyDeclaration(name="release", kind="reference", declared_type=threading.Event),
                ),
            )
        )
        block = cast(GeneratedCallableReference, result.export_name_to_value["block"])
        context = contextvars.copy_context()

        def run_block() -> None:
            try:
                thread_outcome.append(context.run(block))
            except Exception as exception:  # noqa: BLE001
                thread_outcome.append(exception)

        thread = threading.Thread(target=run_block, daemon=True)
        thread.start()
        started.wait(timeout=5)
        return EchoResult(value="returned while a branch is active")

    with pytest.raises(RunFailedError) as captured:
        run_body(body, runner=runner, handle=handle, run_entry_grant=Grant.of(started=started, release=release))
    release.set()
    assert captured.value.terminal_kind == "host_termination"
    assert len(termination.request_list) == 1
    anomalies = [entry.event for entry in handle.ledger.select(TraceAnomalyEvent)]
    assert [anomaly.anomaly_kind for anomaly in anomalies] == ["interior_branch_active_at_run_end"]  # type: ignore[union-attr]
    outcomes = [entry.event for entry in handle.ledger.select(DispatchOutcomeEvent)]
    assert [outcome.status for outcome in outcomes] == ["success", "host_termination"]  # type: ignore[union-attr]


def _start_blocking_branch(release: threading.Event, started: threading.Event) -> None:
    result = dispatch(
        CodeProposal(
            source="def block() -> int:\n    started.set()\n    release.wait()\n    return 1\n",
            export_name_tuple=("block",),
            dependency_tuple=(
                DependencyDeclaration(name="started", kind="reference", declared_type=threading.Event),
                DependencyDeclaration(name="release", kind="reference", declared_type=threading.Event),
            ),
        )
    )
    block = cast(GeneratedCallableReference, result.export_name_to_value["block"])
    context = contextvars.copy_context()
    threading.Thread(target=lambda: context.run(block), daemon=True).start()
    started.wait(timeout=5)


def test_unestablished_cessation_leaves_the_run_without_a_run_end_seam() -> None:
    termination = ScriptedTermination(ceased=False)
    runner = make_runner(termination=termination)
    handle = RunHandle()
    release = threading.Event()
    started = threading.Event()

    def body(run_input: EchoInput) -> EchoResult:
        _start_blocking_branch(release, started)
        return EchoResult()

    try:
        with pytest.raises(RunTerminationError) as captured:
            run_body(body, runner=runner, handle=handle, run_entry_grant=Grant.of(started=started, release=release))
    finally:
        release.set()
    invocation = next(entry.event for entry in handle.ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation")  # type: ignore[union-attr]
    assert captured.value.run_id == handle.ledger.run_id
    assert captured.value.nonterminal_node_id_tuple == (invocation.dispatch_node_id,)  # type: ignore[union-attr]
    assert handle.ledger.select(RunEndEvent) == ()
    assert handle.ledger.dispatch_status(invocation.dispatch_node_id) == "nonterminal"  # type: ignore[union-attr]
    anomaly_kinds = [entry.event.anomaly_kind for entry in handle.ledger.select(TraceAnomalyEvent)]  # type: ignore[union-attr]
    assert anomaly_kinds == ["interior_branch_active_at_run_end", "branch_cessation_unestablished"]
    assert handle.ledger.find(captured.value.anomaly_event_id) is not None
    assert len(termination.request_list) == 1


def test_host_directed_termination_without_cessation_is_not_a_run_end() -> None:
    termination = ScriptedTermination(ceased=False)
    runner = make_runner(termination=termination)
    handle = RunHandle()
    release = threading.Event()
    started = threading.Event()

    def body(run_input: EchoInput) -> EchoResult:
        _start_blocking_branch(release, started)
        handle.terminate("operator request")
        return EchoResult()

    try:
        with pytest.raises(RunTerminationError):
            run_body(body, runner=runner, handle=handle, run_entry_grant=Grant.of(started=started, release=release))
    finally:
        release.set()
    assert handle.ledger.select(RunEndEvent) == ()
    assert len(termination.request_list) == 2


def test_host_directed_termination_with_established_cessation_ends_the_run() -> None:
    termination = ScriptedTermination(ceased=True)
    runner = make_runner(termination=termination)
    handle = RunHandle()
    release = threading.Event()
    started = threading.Event()

    def body(run_input: EchoInput) -> EchoResult:
        _start_blocking_branch(release, started)
        handle.terminate("operator request")
        return EchoResult()

    try:
        with pytest.raises(RunFailedError) as captured:
            run_body(body, runner=runner, handle=handle, run_entry_grant=Grant.of(started=started, release=release))
    finally:
        release.set()
    assert captured.value.terminal_kind == "host_termination"
    statuses = [entry.event.status for entry in handle.ledger.select(DispatchOutcomeEvent)]  # type: ignore[union-attr]
    assert statuses == ["success", "host_termination"]
    assert len(termination.request_list) == 1


def test_handle_termination_presents_the_active_run_root_to_the_platform_and_follows_its_report() -> None:
    termination = ScriptedTermination(ceased=False)
    runner = make_runner(termination=termination)
    handle = RunHandle()
    executor = ScriptedExecutor(responses=[StepCandidate(binding_name_to_value={"label": "a"})])
    observed: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        observed.append(handle.terminate("operator request"))
        try:
            _judge(run_input.text)
        except NaturalExecutionError as failure:
            observed.append(failure.failure_kind)
        return EchoResult(value="returned on its own")

    with pytest.raises(RunFailedError) as captured:
        run_body(body, runner=runner, step_executor=executor, handle=handle)
    assert captured.value.terminal_kind == "host_termination"
    assert observed == [False, "terminated"]
    assert len(termination.request_list) == 1
    request = termination.request_list[0]
    assert request.affected_node_id_tuple == (request.target_node_id,)
    completion = handle.ledger.select(TerminationCompletionEvent)[0].event
    assert isinstance(completion, TerminationCompletionEvent)
    assert completion.ceased is False
    assert completion.affected_node_id_tuple == request.affected_node_id_tuple
    assert handle.ledger.select(TraceAnomalyEvent) == ()
    kinds = event_kinds(handle.ledger.entries)
    assert kinds.index("termination_completion") < kinds.index("run_end")
    assert kinds.count("run_end") == 1


def test_handle_termination_with_established_cessation_records_the_run_end_before_the_scaffold_returns() -> None:
    termination = ScriptedTermination(ceased=True)
    runner = make_runner(termination=termination)
    handle = RunHandle()
    observed: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        observed.append(handle.terminate("operator request"))
        observed.append(len(handle.ledger.select(RunEndEvent)))
        with pytest.raises(DispatchRejectedError) as rejected:
            dispatch(CodeProposal(source="def f() -> int:\n    return 1\n", export_name_tuple=("f",)))
        observed.append(rejected.value.reason)
        return EchoResult(value="ignored")

    with pytest.raises(RunFailedError) as captured:
        run_body(body, runner=runner, handle=handle)
    assert observed == [True, 1, "run_ended"]
    assert captured.value.terminal_kind == "host_termination"
    assert termination.request_list[0].affected_node_id_tuple == (termination.request_list[0].target_node_id,)
    run_end_entries = handle.ledger.select(RunEndEvent)
    assert len(run_end_entries) == 1
    assert captured.value.terminal_event_id == run_end_entries[0].event_id


def test_termination_requested_before_the_run_starts_admits_no_interior_execution() -> None:
    handle = RunHandle()
    handle.terminate("cancelled before start")
    executed: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        executed.append("body")
        return EchoResult()

    with pytest.raises(RunFailedError) as captured:
        run_body(body, runner=make_runner(termination=ScriptedTermination(ceased=False)), handle=handle)
    assert executed == []
    assert captured.value.terminal_kind == "host_termination"
    assert event_kinds(handle.ledger.entries) == ["run_entry", "termination_request", "termination_completion", "run_end"]


def test_accepted_run_result_is_the_validated_value_and_is_recorded() -> None:
    from nighthawk.agent import RunContract, ScaffoldComposition

    composition = ScaffoldComposition(
        run_contract=RunContract(input_type=str, result_type=int),
        body=lambda run_input: "123",  # type: ignore[arg-type, return-value]
        composition_perimeter_policy=PerimeterPolicy.permitting_all(),
    )
    handle = RunHandle()
    result = replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate())).run(composition, "input", handle=handle)
    assert result == 123
    assert isinstance(result, int)
    run_end = handle.ledger.entries[-1].event
    assert isinstance(run_end, RunEndEvent)
    assert run_end.result == 123
    assert run_end.result_type_name == "builtins.int"


def test_termination_during_result_validation_never_returns_a_value() -> None:
    from typing import Annotated

    from pydantic import AfterValidator

    from nighthawk.agent import RunContract, ScaffoldComposition

    validating = threading.Event()
    terminated = threading.Event()

    def wait_for_termination(value: int) -> int:
        validating.set()
        assert terminated.wait(timeout=5)
        return value

    slow_result_type = Annotated[int, AfterValidator(wait_for_termination)]
    handle = RunHandle()
    termination = ScriptedTermination(ceased=True)
    composition = ScaffoldComposition(
        run_contract=RunContract(input_type=str, result_type=slow_result_type),  # type: ignore[arg-type]
        body=lambda run_input: 1,  # type: ignore[arg-type, return-value]
        composition_perimeter_policy=PerimeterPolicy.permitting_all(),
    )
    outcomes: list[object] = []

    def terminate_when_validating() -> None:
        validating.wait(timeout=5)
        outcomes.append(handle.terminate("operator request"))
        terminated.set()

    threading.Thread(target=terminate_when_validating, daemon=True).start()
    with pytest.raises(RunFailedError) as captured:
        replace(make_runner(termination=termination), step_executor=ScriptedExecutor(default_response=StepCandidate())).run(
            composition, "input", handle=handle
        )
    assert outcomes == [True]
    assert captured.value.terminal_kind == "host_termination"
    run_end_entries = handle.ledger.select(RunEndEvent)
    assert len(run_end_entries) == 1
    assert isinstance(run_end_entries[0].event, RunEndEvent)
    assert run_end_entries[0].event.terminal_kind == "host_termination"
    assert captured.value.terminal_event_id == run_end_entries[0].event_id


def test_run_input_is_the_validated_value() -> None:
    from nighthawk.agent import RunContract, ScaffoldComposition

    received: list[object] = []

    def body(run_input: int) -> int:
        received.append(run_input)
        return run_input

    composition = ScaffoldComposition(
        run_contract=RunContract(input_type=int, result_type=int),
        body=body,
        composition_perimeter_policy=PerimeterPolicy.permitting_all(),
    )
    result = replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate())).run(composition, "123")  # type: ignore[arg-type]
    assert received == [123]
    assert isinstance(received[0], int)
    assert result == 123


def test_run_result_whose_snapshot_fails_is_still_recorded_and_returned() -> None:
    from nighthawk.agent import RunContract, ScaffoldComposition

    class Unsnapshottable:
        def __nighthawk_ledger_snapshot__(self) -> object:
            raise ValueError("no audit representation")

    value = Unsnapshottable()
    composition = ScaffoldComposition(
        run_contract=RunContract(input_type=str, result_type=object),
        body=lambda run_input: value,  # type: ignore[arg-type, return-value]
        composition_perimeter_policy=PerimeterPolicy.permitting_all(),
    )
    handle = RunHandle()
    result = replace(make_runner(), step_executor=ScriptedExecutor(default_response=StepCandidate())).run(composition, "input", handle=handle)
    assert result is value
    run_end = handle.ledger.entries[-1].event
    assert isinstance(run_end, RunEndEvent)
    assert run_end.terminal_kind == "accepted"
    assert str(run_end.result).startswith("<snapshot failed ")


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_run_application_class_contracts_preserve_identity_and_reject_wrong_values(asynchronous: bool) -> None:
    from nighthawk.agent.runtime import AsyncScaffoldComposition, RunContract, ScaffoldComposition

    class Document:
        pass

    document = Document()
    returned: object = document
    observed: list[Document] = []

    def body(value: Document) -> Document:
        observed.append(value)
        return cast(Document, returned)

    async def async_body(value: Document) -> Document:
        return body(value)

    contract = RunContract(Document, Document)
    runner = make_runner()

    async def invoke(value: object, handle: RunHandle) -> Document:
        if asynchronous:
            return await runner.run(AsyncScaffoldComposition(contract, async_body), cast(Document, value), handle=handle)
        return runner.run(ScaffoldComposition(contract, body), cast(Document, value), handle=handle)

    handle = RunHandle()
    assert await invoke(document, handle) is document
    assert observed == [document]
    assert len(handle.ledger.select(RunEndEvent)) == 1
    rejected = RunHandle()
    with pytest.raises(RunRejectedError) as failure:
        await invoke(7, rejected)
    assert failure.value.reason == "input_type_invalid"
    assert rejected.run_id is None and observed == [document]
    returned = 7
    failed = RunHandle()
    with pytest.raises(RunFailedError) as failure:
        await invoke(document, failed)
    assert failure.value.terminal_kind == "validation_rejection"
    assert len(failed.ledger.select(RunEndEvent)) == 1
