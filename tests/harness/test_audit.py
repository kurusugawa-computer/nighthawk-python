from __future__ import annotations

import asyncio
import importlib
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

import nighthawk as nh
from nighthawk.core.expression import evaluate_expression, evaluate_expression_async
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.harness.context import current_capabilities, current_executor_step_admission, scope
from nighthawk.harness.dispatch import dispatch
from nighthawk.harness.errors import NaturalExecutionError, PerimeterEffectRejectedError, RunFailedError
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import (
    DispatchEvent,
    DispatchOutcomeEvent,
    NaturalExecutionOutcomeEvent,
    PerimeterEffectAttemptEvent,
    PerimeterEffectCompletionEvent,
    TraceAnomalyEvent,
)
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from tests.support.harness import make_runner
from tests.support.runs import EchoInput, EchoResult, run_body


def _runner() -> Runner:
    return replace(make_runner(), perimeter_confinement=None)


def test_default_dispatch_preserves_objects_without_external_confinement() -> None:
    handle = RunHandle()
    values: list[int] = []

    def body(value: EchoInput) -> EchoResult:
        append = dispatch(
            CodeProposal(source="def append(values):\n    values.append(42)\n    return values\n", export_name_tuple=("append",))
        ).export_name_to_value["append"]
        assert append(values) is values  # type: ignore[operator]
        return EchoResult()

    run_body(body, runner=_runner(), handle=handle)
    assert values == [42]
    assert len(handle.ledger.select(DispatchEvent)) == 2
    assert all(entry.event.confinement_identity is None for entry in handle.ledger.select(DispatchEvent))  # type: ignore[union-attr]
    assert not handle.ledger.select(TraceAnomalyEvent)


@pytest.mark.parametrize("operation", ["write", "delete", "rename", "connect", "sendto", "process"])
def test_direct_effect_is_rejected_before_effect_and_attributed(tmp_path: Path, operation: str) -> None:
    target = tmp_path / "sensitive-name-must-not-enter-ledger"
    target.write_text("original")
    moved = tmp_path / "moved"
    with socket.socket() as connection, socket.socket(type=socket.SOCK_DGRAM) as datagram:
        operation_name_to_action: dict[str, Callable[[], object]] = {
            "write": lambda: target.write_text("changed"),
            "delete": target.unlink,
            "rename": lambda: target.rename(moved),
            "connect": lambda: connection.connect(("127.0.0.1", 9)),
            "sendto": lambda: datagram.sendto(b"message", ("127.0.0.1", 9)),
            "process": lambda: subprocess.run([sys.executable, "-c", "raise SystemExit(99)"], check=True),
        }
        handle = RunHandle()

        def body(value: EchoInput) -> EchoResult:
            with pytest.raises(PerimeterEffectRejectedError) as captured:
                operation_name_to_action[operation]()
            assert captured.value.reason == "unmediated_python_effect"
            return EchoResult()

        run_body(body, runner=_runner(), handle=handle)
    assert target.read_text() == "original"
    assert not moved.exists()
    anomalies = handle.ledger.select(TraceAnomalyEvent)
    assert len(anomalies) == 1
    assert anomalies[0].event.anomaly_kind == "unmediated_perimeter_effect"  # type: ignore[union-attr]
    assert target.name not in repr(anomalies)
    assert not handle.ledger.select(PerimeterEffectAttemptEvent)
    # Operator execution outside a run retains ordinary Python effects.
    target.write_text("operator")
    assert target.read_text() == "operator"


def test_generated_direct_write_fails_its_dispatch_and_leaves_target_untouched(tmp_path: Path) -> None:
    target = tmp_path / "generated.txt"
    handle = RunHandle()

    def body(value: EchoInput) -> EchoResult:
        function = dispatch(
            CodeProposal(source="def write(path):\n    with open(path, 'w') as stream:\n        stream.write('bad')\n", export_name_tuple=("write",))
        ).export_name_to_value["write"]
        with pytest.raises(PerimeterEffectRejectedError):
            function(str(target))  # type: ignore[operator]
        return EchoResult()

    run_body(body, runner=_runner(), handle=handle)
    assert not target.exists()
    outcomes = handle.ledger.select(DispatchOutcomeEvent)
    assert [entry.event.status for entry in outcomes] == ["success", "failure"]  # type: ignore[union-attr]
    assert len(handle.ledger.select(TraceAnomalyEvent)) == 1


def test_capability_audits_semantic_write_and_owns_temporary_cleanup(tmp_path: Path) -> None:
    target = tmp_path / "saved"
    temporary = tmp_path / "temporary"
    handle = RunHandle()

    def save() -> None:
        assert len(handle.ledger.select(PerimeterEffectAttemptEvent)) == 1
        temporary.write_text("temporary")
        try:
            target.write_text("saved")
            with scope(Grant.EMPTY), pytest.raises(PerimeterEffectRejectedError):
                target.write_text("nested scope must not inherit gateway permission")
        finally:
            temporary.unlink()

    capability = Capability(
        name="save",
        function=save,
        authority_boundary="selected artifact",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset(("durable_write_back",))),
    )

    def body(value: EchoInput) -> EchoResult:
        current_capabilities()["save"]()  # type: ignore[operator]
        with pytest.raises(PerimeterEffectRejectedError):
            target.write_text("after capability")
        return EchoResult()

    run_body(body, runner=_runner(), run_entry_grant=Grant.of(capability), handle=handle)
    assert target.read_text() == "saved"
    assert not temporary.exists()
    assert len(handle.ledger.select(PerimeterEffectCompletionEvent)) == 1


async def test_capability_permission_expires_in_copied_task_context(tmp_path: Path) -> None:
    target = tmp_path / "late"
    resume = asyncio.Event()
    pending: list[asyncio.Task[None]] = []

    async def later() -> None:
        await resume.wait()
        with pytest.raises(PerimeterEffectRejectedError):
            target.write_text("late")

    async def schedule() -> None:
        pending.append(asyncio.create_task(later()))

    capability = Capability(
        name="schedule",
        function=schedule,
        authority_boundary="owned background operation",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset(("durable_write_back",))),
    )

    async def body(value: int) -> int:
        await current_capabilities()["schedule"]()  # type: ignore[operator]
        resume.set()
        await pending[0]
        return value

    composition = AsyncScaffoldComposition(
        RunContract(int, int), body, run_entry_grant=Grant.of(capability), composition_perimeter_policy=_runner().perimeter_policy
    )
    assert await _runner().run(composition, 1) == 1
    assert not target.exists()


def test_nonperimeter_capability_cannot_write(tmp_path: Path) -> None:
    target = tmp_path / "unexpected"

    def calculate() -> int:
        target.write_text("undeclared")
        return 1

    capability = Capability(name="calculate", function=calculate, authority_boundary="arithmetic")

    def body(value: EchoInput) -> EchoResult:
        with pytest.raises(PerimeterEffectRejectedError):
            current_capabilities()["calculate"]()  # type: ignore[operator]
        return EchoResult()

    run_body(body, runner=_runner(), run_entry_grant=Grant.of(capability))
    assert not target.exists()


def test_import_without_cache_does_not_authorize_module_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "audit_import_pure.py").write_text("value = 42\n")
    target = tmp_path / "import-effect"
    (tmp_path / "audit_import_effect.py").write_text(f"from pathlib import Path\nPath({str(target)!r}).write_text('bad')\n")
    handle = RunHandle()

    def body(value: EchoInput) -> EchoResult:
        assert importlib.import_module("audit_import_pure").value == 42
        with pytest.raises(PerimeterEffectRejectedError):
            importlib.import_module("audit_import_effect")
        return EchoResult()

    try:
        run_body(body, runner=_runner(), handle=handle)
        assert not (tmp_path / "__pycache__").exists()
        assert not target.exists()
        assert len(handle.ledger.select(TraceAnomalyEvent)) == 1
    finally:
        sys.modules.pop("audit_import_pure", None)
        sys.modules.pop("audit_import_effect", None)


@nh.function
async def _provider_step() -> None:
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Complete the provider step.
    """


async def test_custom_provider_request_scope_expires_before_expression_evaluation(tmp_path: Path) -> None:

    target = tmp_path / "expression-write"

    class Executor:
        admits_executor_steps = True

        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            step_context = request.step_context
            admission = current_executor_step_admission()
            with admission.request():
                assert socket.getaddrinfo("127.0.0.1", 80)
            admission.observe()
            with pytest.raises(PerimeterEffectRejectedError):
                socket.getaddrinfo("127.0.0.1", 80)
            with pytest.raises(PerimeterEffectRejectedError):
                await evaluate_expression_async(step_context, f"open({str(target)!r}, 'w')")
            return StepCandidate(outcome=Pass(), binding_name_to_value={})

    async def body(value: int) -> int:
        await _provider_step()
        return value

    handle = RunHandle()
    runner = replace(_runner(), step_executor=Executor())
    with pytest.raises(RunFailedError):
        await runner.run(AsyncScaffoldComposition(RunContract(int, int), body, function_definition_tuple=(_provider_step,)), 7, handle=handle)
    assert not target.exists()
    assert len(handle.ledger.select(TraceAnomalyEvent)) == 2
    assert handle.ledger.select(NaturalExecutionOutcomeEvent)[0].event.executor_usage.request_count == 1  # type: ignore[union-attr]


async def test_an_operation_inside_a_provider_request_scope_is_a_mediation_bypass(tmp_path: Path) -> None:

    target = tmp_path / "expression-write"

    class Executor:
        admits_executor_steps = True

        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            with current_executor_step_admission().request():
                await evaluate_expression_async(request.step_context, f"open({str(target)!r}, 'w')")
            return StepCandidate(outcome=Pass(), binding_name_to_value={})

    async def body(value: int) -> int:
        with pytest.raises(NaturalExecutionError) as failure:
            await _provider_step()
        assert failure.value.failure_kind == "mediation_bypass"
        return value

    handle = RunHandle()
    runner = replace(_runner(), step_executor=Executor())
    assert await runner.run(AsyncScaffoldComposition(RunContract(int, int), body, function_definition_tuple=(_provider_step,)), 7, handle=handle) == 7
    assert not target.exists()
    assert [entry.event.anomaly_kind for entry in handle.ledger.select(TraceAnomalyEvent)] == ["operation_under_gateway_allowance"]  # type: ignore[union-attr]
    [outcome] = [entry.event for entry in handle.ledger.select(NaturalExecutionOutcomeEvent)]
    assert isinstance(outcome, NaturalExecutionOutcomeEvent) and outcome.failure_kind == "mediation_bypass"


@nh.function
def _capability_step() -> None:
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Complete the capability step.
    """


def test_a_natural_function_inside_a_perimeter_capability_starts_without_its_allowance(tmp_path: Path) -> None:

    expression_target = tmp_path / "expression-write"
    capability_target = tmp_path / "capability-write"
    rejection_list: list[PerimeterEffectRejectedError] = []

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            try:
                evaluate_expression(request.step_context, f"open({str(expression_target)!r}, 'w')")
            except PerimeterEffectRejectedError as rejection:
                rejection_list.append(rejection)
            return StepCandidate(outcome=Pass(), binding_name_to_value={})

    def save() -> None:
        with pytest.raises(NaturalExecutionError):
            _capability_step()
        capability_target.write_text("saved")

    capability = Capability(
        name="save",
        function=save,
        authority_boundary="selected artifact",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset(("durable_write_back",))),
    )

    def body(value: EchoInput) -> EchoResult:
        current_capabilities()["save"]()  # type: ignore[operator]
        return EchoResult()

    handle = RunHandle()
    run_body(
        body,
        runner=_runner(),
        step_executor=Executor(),
        run_entry_grant=Grant.of(capability),
        function_definitions=(_capability_step,),
        handle=handle,
    )
    assert len(rejection_list) == 1 and not expression_target.exists()
    assert capability_target.read_text() == "saved"
    assert [entry.event.anomaly_kind for entry in handle.ledger.select(TraceAnomalyEvent)] == ["unmediated_perimeter_effect"]  # type: ignore[union-attr]


@pytest.mark.parametrize("interruption", [ValueError, KeyboardInterrupt])
def test_failed_capability_revokes_permission_and_records_completion(tmp_path: Path, interruption: type[BaseException]) -> None:
    target = tmp_path / "partial-effect"
    handle = RunHandle()

    def save() -> None:
        target.write_text("attempted")
        raise interruption()

    capability = Capability(
        name="save",
        function=save,
        authority_boundary="selected artifact",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset(("durable_write_back",))),
    )

    def body(value: EchoInput) -> EchoResult:
        with pytest.raises(interruption):
            current_capabilities()["save"]()  # type: ignore[operator]
        with pytest.raises(PerimeterEffectRejectedError):
            target.write_text("after failure")
        return EchoResult()

    run_body(body, runner=_runner(), run_entry_grant=Grant.of(capability), handle=handle)
    assert target.read_text() == "attempted"
    completion = handle.ledger.select(PerimeterEffectCompletionEvent)[0].event
    assert isinstance(completion, PerimeterEffectCompletionEvent) and completion.status == "failure"
