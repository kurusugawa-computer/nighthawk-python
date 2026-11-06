from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from nighthawk.core.usage import ExecutionUsage
from nighthawk.harness._audit import host_service_scope
from nighthawk.harness.errors import CapabilityRejectedError, GrantError
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import RunLedger
from nighthawk.harness.resources import ExecutorStepAdmission
from nighthawk.harness.termination import TerminationCompletion

if TYPE_CHECKING:
    from nighthawk.harness._host import Host, _NaturalExecutionRecord


@dataclass(frozen=True)
class AttributionNode:
    """One node of the attribution tree: the run root or one dispatch node."""

    node_id: str
    parent_node_id: str | None
    recursion_depth: int
    kind: Literal["run_root", "dispatch"]


@dataclass(frozen=True)
class ExecutionContext:
    """The grant, attribution node, and generated source in effect for the current execution.

    ``generated_source`` identifies the materialized source whose generated
    execution is running under ``node``; it is ``None`` for ratified interior
    execution and during materialization.
    """

    grant: Grant
    node: AttributionNode
    generated_source: object | None = None


_current_host_var: ContextVar[Host | None] = ContextVar("nighthawk_harness_host", default=None)


_execution_context_var: ContextVar[ExecutionContext | None] = ContextVar("nighthawk_harness_execution_context", default=None)


_active_natural_execution_var: ContextVar[_NaturalExecutionRecord | None] = ContextVar("nighthawk_harness_active_natural_execution", default=None)


def current_host() -> Host | None:
    """Return the host of the active agent run, or ``None`` outside a run."""

    return _current_host_var.get()


def current_grant() -> Grant:
    """Return the grant in effect for the current execution.

    Raises:
        CapabilityRejectedError: outside an active agent run.
    """

    context = _execution_context_var.get()
    if context is None:
        raise CapabilityRejectedError(capability_name="current_grant", reason_code="no_active_run")
    return context.grant


def current_capabilities() -> dict[str, Callable[..., object]]:
    """Return mediated capability callables for the current grant.

    Available throughout an agent run, including ordinary Python between Natural steps.
    Retained callables recheck the active grant and run lifetime on every invocation.
    """
    grant = current_grant()
    host = current_host()
    assert host is not None
    return host.projection(grant)


def current_run_ledger() -> RunLedger:
    """Return the run ledger of the active agent run for deterministic queries."""

    host = current_host()
    if host is None:
        raise CapabilityRejectedError(capability_name="current_run_ledger", reason_code="no_active_run")
    return host.ledger


@contextmanager
def scope(grant: Grant) -> Generator[Grant]:
    """Declare the grant in effect for the Natural functions and dispatches within.

    The grant must be an attenuation of the submitting execution's current
    grant: every capability must already be held under the same name. Plain
    references may be added freely because they carry no authority.

    Raises:
        GrantError: when the grant widens authority or no run is active.
    """

    host = current_host()
    context = _execution_context_var.get()
    if host is None or context is None:
        raise GrantError(reason="no_active_run")
    widening = grant.widening_over(context.grant)
    if widening:
        raise GrantError(reason="authority_widening", name_tuple=widening)
    with host.execution_scope(grant, context.node, generated_source=context.generated_source):
        yield grant


class _ExecutorStepAdmission:
    """Per-step admission handed to executors that present their own LLM requests."""

    def __init__(self, host: Host, record: _NaturalExecutionRecord) -> None:
        self._host = host
        self._record = record

    def admit(self) -> None:
        self._host.admit_executor_step(self._record)

    @contextmanager
    def request(self) -> Generator[None]:
        """Admit one custom executor request and execute its provider communication as host infrastructure until exit."""
        self.admit()
        with host_service_scope():
            yield

    def observe(self, usage: ExecutionUsage | None = None, *, model_name: str | None = None) -> None:
        self._host.observe_executor_step(self._record, usage, model_name=model_name)


def current_executor_step_admission() -> ExecutorStepAdmission:
    """Return the host admission of the executor steps of the current Natural execution.

    A step executor that declares ``admits_executor_steps = True`` calls
    ``with admission.request():`` around each provider request and ``observe(usage)`` after each
    response (see ``nighthawk.harness.resources.ExecutorStepAdmission``).

    Raises:
        CapabilityRejectedError: outside a host-mediated Natural execution.
    """

    host = current_host()
    record = _active_natural_execution_var.get()
    if host is None or record is None:
        raise CapabilityRejectedError(capability_name="executor_step_admission", reason_code="no_active_run")
    return _ExecutorStepAdmission(host, record)


def _terminate_descendant(dispatch_node_id: str) -> TerminationCompletion:
    """Force termination of one descendant dispatch branch and its descendants."""

    host = current_host()
    if host is None:
        raise CapabilityRejectedError(capability_name="terminate_descendant", reason_code="no_active_run")
    return host.terminate_descendant(dispatch_node_id)


def terminate_descendant_capability(*, name: str = "terminate_descendant") -> Capability:
    """Return the host capability that requests descendant termination.

    Include it in a grant to let that execution request forced termination of
    dispatch branches below its own attribution node. Completion is reported
    only after the platform establishes cessation and the affected outcomes are
    recorded.
    """

    return Capability(
        name=name,
        function=_terminate_descendant,
        authority_boundary="terminate dispatch branches below the requesting execution only",
    )
