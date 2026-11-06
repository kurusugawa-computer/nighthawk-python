"""Shared fixtures: an OpenTelemetry provider and platform service doubles.

OpenTelemetry proxy tracers resolve the provider when spans start.
Platform doubles stand in for the external guarantees the library consumes: confinement confirmation, cessation reports, and an in-memory perimeter audit store.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nighthawk.core.step_request import StepCandidate
from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.confinement import ConfinementConfirmation, ConfinementRequest
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import ResourceAllocation, bounded_resource_allocation
from nighthawk.harness.run_contract import RunIdentity
from nighthawk.harness.runner import Runner
from nighthawk.harness.termination import TerminationReport, TerminationRequest
from nighthawk.harness.testing import InMemoryPerimeterAuditStore

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)


@pytest.fixture
def in_memory_span_exporter() -> Iterator[InMemorySpanExporter]:
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


class ConfirmedConfinement:
    """Confinement double that confirms every dispatch and records the requests."""

    def __init__(self) -> None:
        self.request_list: list[ConfinementRequest] = []

    def confirm(self, request: ConfinementRequest) -> ConfinementConfirmation | None:
        self.request_list.append(request)
        return ConfinementConfirmation(confinement_identity="test-confinement")


class ScriptedTermination:
    """Termination double whose cessation report is decided by the test."""

    def __init__(self, *, ceased: bool = True) -> None:
        self.ceased = ceased
        self.request_list: list[TerminationRequest] = []

    def terminate(self, request: TerminationRequest) -> TerminationReport:
        self.request_list.append(request)
        return TerminationReport(ceased=self.ceased, detail="scripted")


class FailingAuditStore:
    """Audit store double that refuses every record."""

    def record(self, record: object) -> None:
        raise OSError("durable storage unavailable")


def make_runner(
    *,
    perimeter_policy: PerimeterPolicy | None = None,
    allocation_factory: Callable[[RunIdentity], ResourceAllocation] | None = None,
    confinement: object | None = None,
    audit_store: object | None = None,
    termination: object | None = None,
    accepts_aggregate_executor_accounting: bool = True,
) -> Runner:
    """Runner double; it accepts aggregate executor accounting because most tests use scripted executors."""

    return Runner(
        step_executor=ScriptedExecutor(default_response=StepCandidate()),
        perimeter_policy=perimeter_policy if perimeter_policy is not None else PerimeterPolicy.permitting_all(),
        resource_allocation_factory=allocation_factory
        if allocation_factory is not None
        else (lambda run_identity: bounded_resource_allocation(identity=f"allocation-{run_identity.run_id}")),
        perimeter_confinement=confinement if confinement is not None else ConfirmedConfinement(),  # type: ignore[arg-type]
        perimeter_audit_store=audit_store if audit_store is not None else InMemoryPerimeterAuditStore(),  # type: ignore[arg-type]
        termination_mechanism=termination if termination is not None else ScriptedTermination(),  # type: ignore[arg-type]
        accepts_aggregate_executor_accounting=accepts_aggregate_executor_accounting,
    )


@pytest.fixture
def runner() -> Runner:
    return make_runner()
