from __future__ import annotations

import contextvars
import threading
from typing import cast

import pytest

from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import CallbackExecutor
from nighthawk.harness.context import current_capabilities, current_run_ledger, terminate_descendant_capability
from nighthawk.harness.dispatch import GeneratedCallableReference, dispatch
from nighthawk.harness.errors import CapabilityRejectedError, NaturalExecutionError, TerminationError
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import (
    DispatchEvent,
    DispatchOutcomeEvent,
    NaturalExecutionOutcomeEvent,
    TerminationCompletionEvent,
    TerminationRequestEvent,
)
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.termination import TerminationCompletion
from tests.support.harness import ScriptedTermination, make_runner
from tests.support.runs import EchoInput, EchoResult, run_body

_BLOCKING_SOURCE = "def block(name: str) -> str:\n    started.set()\n    release.wait()\n    return greet(name)\n"


def _greet(name: str) -> str:
    return f"hello {name}"


def _terminate(node_id: str) -> TerminationCompletion:
    wrapper = current_capabilities()["terminate_descendant"]
    return wrapper(node_id)  # type: ignore[operator, return-value]


def _start_blocked_branch(release: threading.Event, started: threading.Event, outcome_list: list[object]) -> str:
    result = dispatch(
        CodeProposal(
            source=_BLOCKING_SOURCE,
            export_name_tuple=("block",),
            dependency_tuple=(
                DependencyDeclaration(name="started", kind="reference", declared_type=threading.Event),
                DependencyDeclaration(name="release", kind="reference", declared_type=threading.Event),
                DependencyDeclaration(name="greet", kind="capability", declared_type="(name: str) -> str"),
            ),
            effect_footprint=EffectFootprint(capability_name_set=frozenset({"greet"})),
        )
    )
    block = cast(GeneratedCallableReference, result.export_name_to_value["block"])
    context = contextvars.copy_context()

    def run_block() -> None:
        try:
            outcome_list.append(context.run(block, "ann"))
        except Exception as exception:  # noqa: BLE001
            outcome_list.append(exception)

    threading.Thread(target=run_block, daemon=True).start()
    started.wait(timeout=5)
    ledger = current_run_ledger()
    return [entry.event for entry in ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation"][-1].dispatch_node_id  # type: ignore[union-attr]


def _grant() -> Grant:
    return Grant.of(
        Capability(name="greet", function=_greet, authority_boundary="pure"),
        terminate_descendant_capability(),
        started=threading.Event(),
        release=threading.Event(),
    )


def test_parent_terminates_a_descendant_branch_and_the_host_records_its_outcome() -> None:
    termination = ScriptedTermination(ceased=True)
    runner = make_runner(termination=termination)
    grant = _grant()
    release: threading.Event = grant.references["release"]  # type: ignore[assignment]
    started: threading.Event = grant.references["started"]  # type: ignore[assignment]
    thread_outcome: list[object] = []
    completion_list: list[TerminationCompletion] = []

    def body(run_input: EchoInput) -> EchoResult:
        node_id = _start_blocked_branch(release, started, thread_outcome)
        completion_list.append(_terminate(node_id))
        release.set()
        for _ in range(200):
            if thread_outcome:
                break
            threading.Event().wait(0.01)
        return EchoResult(value=node_id)

    handle = RunHandle()
    result = run_body(body, runner=runner, run_entry_grant=grant, handle=handle)
    completion = completion_list[0]
    assert completion.target_node_id == result.value
    assert completion.affected_node_id_tuple == (result.value,)
    assert termination.request_list[0].affected_node_id_tuple == (result.value,)
    assert handle.ledger.dispatch_status(result.value) == "host_termination"
    request_entry = handle.ledger.select(TerminationRequestEvent)[0]
    completion_entry = handle.ledger.find(completion.completion_event_id)
    assert completion_entry is not None and completion_entry.parent_event_id == request_entry.event_id
    assert isinstance(completion_entry.event, TerminationCompletionEvent) and completion_entry.event.ceased is True
    assert isinstance(thread_outcome[0], CapabilityRejectedError)
    assert thread_outcome[0].reason_code == "node_terminated"
    statuses = [entry.event.status for entry in handle.ledger.select(DispatchOutcomeEvent)]  # type: ignore[union-attr]
    assert statuses == ["success", "host_termination"]


def test_termination_is_not_established_when_the_platform_reports_no_cessation() -> None:
    termination = ScriptedTermination(ceased=False)
    runner = make_runner(termination=termination)
    grant = _grant()
    release: threading.Event = grant.references["release"]  # type: ignore[assignment]
    started: threading.Event = grant.references["started"]  # type: ignore[assignment]
    thread_outcome: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        node_id = _start_blocked_branch(release, started, thread_outcome)
        with pytest.raises(TerminationError) as captured:
            _terminate(node_id)
        assert captured.value.reason == "cessation_not_established"
        assert current_run_ledger().dispatch_status(node_id) == "nonterminal"
        release.set()
        for _ in range(200):
            if thread_outcome:
                break
            threading.Event().wait(0.01)
        return EchoResult(value=str(thread_outcome[0]))

    handle = RunHandle()
    result = run_body(body, runner=runner, run_entry_grant=grant, handle=handle)
    assert result.value == "hello ann"
    completion = handle.ledger.select(TerminationCompletionEvent)[0].event
    assert isinstance(completion, TerminationCompletionEvent) and completion.ceased is False


def test_termination_targets_must_be_known_descendants() -> None:
    grant = Grant.of(terminate_descendant_capability())
    reasons: list[str] = []

    def body(run_input: EchoInput) -> EchoResult:
        with pytest.raises(TerminationError) as unknown:
            _terminate("missing")
        reasons.append(unknown.value.reason)
        root_id = current_run_ledger().entries[0].event_id
        with pytest.raises(TerminationError) as not_descendant:
            _terminate(root_id)
        reasons.append(not_descendant.value.reason)
        return EchoResult()

    run_body(body, runner=make_runner(), run_entry_grant=grant)
    assert reasons == ["unknown_node", "unknown_node"]


def test_terminated_node_cannot_submit_new_operations_and_completed_target_is_trivial() -> None:
    grant = Grant.of(terminate_descendant_capability())

    def body(run_input: EchoInput) -> EchoResult:
        result = dispatch(CodeProposal(source="def one() -> int:\n    return 1\n", export_name_tuple=("one",)))
        completion = _terminate(result.dispatch_node_id)
        return EchoResult(value=str(len(completion.affected_node_id_tuple)))

    assert run_body(body, runner=make_runner(), run_entry_grant=grant).value == "0"


def test_active_natural_execution_in_a_terminated_branch_records_a_terminated_outcome() -> None:
    termination = ScriptedTermination(ceased=True)
    runner = make_runner(termination=termination)
    started = threading.Event()
    release = threading.Event()
    grant = Grant.of(terminate_descendant_capability())
    source = (
        "import nighthawk as nh\n\n"
        "@nh.function\n"
        "def wait_for(text: str) -> str:\n"
        '    label: str = ""\n'
        '    """execute\n'
        "    Read <text> and set <:label>.\n"
        '    """\n'
        "    return label\n"
    )

    def handler(call: StepRequest) -> StepCandidate:
        started.set()
        release.wait(timeout=5)
        return StepCandidate(binding_name_to_value={"label": "late"})

    thread_outcome: list[object] = []

    def body(run_input: EchoInput) -> EchoResult:
        wait_for = cast(
            GeneratedCallableReference, dispatch(CodeProposal(source=source, export_name_tuple=("wait_for",))).export_name_to_value["wait_for"]
        )
        context = contextvars.copy_context()

        def run_wait() -> None:
            try:
                thread_outcome.append(context.run(wait_for, "x"))
            except Exception as exception:  # noqa: BLE001
                thread_outcome.append(exception)

        threading.Thread(target=run_wait, daemon=True).start()
        started.wait(timeout=5)
        ledger = current_run_ledger()
        node_id = next(entry.event for entry in ledger.select(DispatchEvent) if entry.event.dispatch_kind == "invocation").dispatch_node_id  # type: ignore[union-attr]
        _terminate(node_id)
        outcome_before_release = ledger.select(NaturalExecutionOutcomeEvent)[0].event
        assert isinstance(outcome_before_release, NaturalExecutionOutcomeEvent)
        assert outcome_before_release.failure_kind == "terminated"
        release.set()
        for _ in range(200):
            if thread_outcome:
                break
            threading.Event().wait(0.01)
        return EchoResult()

    handle = RunHandle()
    run_body(body, runner=runner, run_entry_grant=grant, step_executor=CallbackExecutor(handler), handle=handle)
    assert isinstance(thread_outcome[0], NaturalExecutionError)
    assert thread_outcome[0].failure_kind == "terminated"
    assert len(handle.ledger.select(NaturalExecutionOutcomeEvent)) == 1
