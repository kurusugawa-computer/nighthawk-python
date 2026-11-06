"""Reusable run configuration, execution, and external service contracts.

Runner owns the executor, perimeter policy, opaque allocation factory, confinement, audit storage, and termination mechanism.
Every admitted run uses the same private host machinery and a fresh allocation.
Async runs start only when awaited.
The default executes in process with limited Python audit checks and no external confinement claim.
The default termination service never claims forced cessation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Coroutine
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, overload, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.usage import RequestUsage, RunUsage

import nighthawk as nh
from nighthawk.runtime.step_executor import StepExecutor

from ._ledger import ExecutorStepObservation, ExecutorUsage, PerimeterEffectAttemptEvent, PerimeterEffectCompletionEvent, generate_ulid
from ._perimeter import EffectFootprint, PerimeterPolicy
from ._run import ApprovedFunctionDefinition, AsyncScaffoldComposition, RunContract, RunHandle, ScaffoldComposition, _run_async, _run_sync

__all__ = [
    "ApprovedFunctionDefinition",
    "AsyncScaffoldComposition",
    "ConfinementConfirmation",
    "ConfinementRequest",
    "ExecutorStepAdmission",
    "ExecutorStepObservation",
    "InMemoryPerimeterAuditStore",
    "PerimeterAuditRecord",
    "PerimeterAuditStore",
    "PerimeterConfinement",
    "ResourceAllocation",
    "ResourceConsumption",
    "ResourceRejection",
    "ResourceRequest",
    "ResourceRequestKind",
    "RunContract",
    "RunHandle",
    "RunIdentity",
    "Runner",
    "ScaffoldComposition",
    "TerminationMechanism",
    "TerminationReport",
    "TerminationRequest",
    "bounded_resource_allocation",
    "unavailable_termination_mechanism",
    "unconfirmed_perimeter_confinement",
]


class _FrozenValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RunIdentity(_FrozenValue):
    run_id: str


type ResourceRequestKind = Literal["dispatch", "natural_execution", "executor_step"]


class ResourceRequest(_FrozenValue):
    """One resource-consuming request presented for admission.

    ``dispatch`` admits one dispatch node, ``natural_execution`` admits one
    Natural execution before its first executor step, and ``executor_step``
    admits one executor step (one LLM response) of an admitted Natural
    execution. The host presents an executor step before its LLM request under
    the ``model_requests`` and ``executor_reports`` observation modes of
    :data:`ExecutorStepObservation`, and otherwise as soon as it learns of the
    step from the executor's metered usage.
    """

    run_id: str
    kind: ResourceRequestKind
    attribution_node_id: str
    recursion_depth: int = Field(ge=0)


class ResourceRejection(_FrozenValue):
    """Typed refusal of a resource request."""

    reason: str


class ResourceConsumption(_FrozenValue):
    """Consumption reported by the host after it metered it.

    An ``executor_step`` consumption reports one observed LLM response as soon
    as the host sees it, so the next step admission can account for it. A
    ``natural_execution`` consumption reports what the execution consumed
    beyond the steps already reported; ``executor_step_count`` repeats the
    number of steps the host presented for admission.
    """

    run_id: str
    kind: ResourceRequestKind
    attribution_node_id: str
    recursion_depth: int = Field(default=0, ge=0)
    executor_step_count: int = Field(default=0, ge=0)
    executor_usage: ExecutorUsage = ExecutorUsage()


@runtime_checkable
class ResourceAllocation(Protocol):
    """Opaque run-scoped resource envelope established by the platform.

    ``admit`` returns ``None`` to admit a request or a :class:`ResourceRejection`
    when the allocation is exhausted for it. ``record`` receives consumption
    after the host metered it.
    """

    @property
    def identity(self) -> str: ...

    def admit(self, request: ResourceRequest) -> ResourceRejection | None: ...

    def record(self, consumption: ResourceConsumption) -> None: ...


"""How the host observes the executor steps of a Natural execution (:data:`ExecutorStepObservation`).

``model_requests``: the step executor is the core ``AgentStepExecutor`` over a
Pydantic AI agent; the host installs a Pydantic AI capability for the
execution, presents the step before every model request (streamed or not) is
sent, and accounts the response usage immediately.
``executor_reports``: the executor declares ``admits_executor_steps = True`` and
calls :func:`nighthawk.agent.current_executor_step_admission` around every LLM
request itself. In both modes the host prevents the request that would exceed
the allocation.
``aggregate``: the executor reports usage only after the Natural execution; the
host presents the steps it learns of afterwards, and an execution that
consumed beyond the allocation ends as a typed failure. This mode does not
prevent consumption inside one execution: a run is admitted in it only when
:attr:`Runner.accepts_aggregate_executor_accounting` is set, which records
that the platform enforces the per-step bound inside its executor or provider,
and the run-entry event records the mode.
"""


@runtime_checkable
class ExecutorStepAdmission(Protocol):
    """Host-side admission of the executor steps of the current Natural execution.

    A step executor that declares ``admits_executor_steps = True`` obtains this
    through :func:`nighthawk.agent.current_executor_step_admission` and uses
    :meth:`request` around each provider request, followed by :meth:`observe` with its usage.
    The request context admits one step and executes provider communication as host infrastructure only until exit;
    tool evaluation belongs outside it, and an operation evaluated inside it fails the Natural execution as a mediation bypass.
    :meth:`admit` alone admits a step without authorizing communication.
    Admission raises the typed
    ``NaturalExecutionError`` of the execution when the allocation refuses the
    step; the executor lets it propagate.
    """

    def admit(self) -> None: ...

    def request(self) -> AbstractContextManager[None]: ...

    def observe(self, usage: RequestUsage | RunUsage | None = None, *, model_name: str | None = None) -> None: ...


class ConfinementRequest(_FrozenValue):
    """What the host asks the platform to confirm before a dispatch."""

    run_id: str
    submitting_node_id: str
    recursion_depth: int = Field(ge=0)
    effect_footprint: EffectFootprint


class ConfinementConfirmation(_FrozenValue):
    """Platform confirmation that generated execution will be confined."""

    confinement_identity: str


@runtime_checkable
class PerimeterConfinement(Protocol):
    """Platform-supplied preventive boundary for generated execution.

    ``confirm`` returns a confirmation when the platform establishes that direct
    perimeter effects are prevented for the dispatch, or ``None`` when it cannot;
    the host then rejects the dispatch before generated execution begins.
    """

    def confirm(self, request: ConfinementRequest) -> ConfinementConfirmation | None: ...


class PerimeterAuditRecord(_FrozenValue):
    """A perimeter-effect event handed to durable storage before ledger commit."""

    run_id: str
    event_id: str
    parent_event_id: str | None
    event: PerimeterEffectAttemptEvent | PerimeterEffectCompletionEvent


@runtime_checkable
class PerimeterAuditStore(Protocol):
    """Durable storage for perimeter-effect attempt and completion events.

    ``record`` must persist the record before returning; raising prevents the
    perimeter effect from being attempted.
    """

    def record(self, record: PerimeterAuditRecord) -> None: ...


class TerminationRequest(_FrozenValue):
    """Forced termination of a dispatch branch and its descendants."""

    run_id: str
    requesting_node_id: str
    target_node_id: str
    affected_node_id_tuple: tuple[str, ...]


class TerminationReport(_FrozenValue):
    """Whether the platform established that the targeted branches ceased."""

    ceased: bool
    detail: str = ""


@runtime_checkable
class TerminationMechanism(Protocol):
    """Platform mechanism that stops branches without their cooperation."""

    def terminate(self, request: TerminationRequest) -> TerminationReport: ...


class _UnconfirmedPerimeterConfinement:
    def confirm(self, request: ConfinementRequest) -> ConfinementConfirmation | None:
        del request
        return None


class _UnavailableTerminationMechanism:
    def terminate(self, request: TerminationRequest) -> TerminationReport:
        del request
        return TerminationReport(ceased=False, detail="no termination mechanism is configured")


def unconfirmed_perimeter_confinement() -> PerimeterConfinement:
    """Return a confinement contract that never confirms, so every dispatch is rejected."""

    return _UnconfirmedPerimeterConfinement()


def unavailable_termination_mechanism() -> TerminationMechanism:
    """Return a termination mechanism that never establishes cessation."""

    return _UnavailableTerminationMechanism()


class InMemoryPerimeterAuditStore:
    """Process-local perimeter audit store.

    This store does not satisfy the durability obligation: its records vanish
    with the process. Use it for tests and local experiments only.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._record_list: list[PerimeterAuditRecord] = []

    def record(self, record: PerimeterAuditRecord) -> None:
        with self._lock:
            self._record_list.append(record)

    @property
    def records(self) -> tuple[PerimeterAuditRecord, ...]:
        with self._lock:
            return tuple(self._record_list)


class _BoundedResourceAllocation:
    def __init__(
        self,
        *,
        identity: str,
        maximum_recursion_depth: int | None,
        maximum_dispatch_count: int | None,
        maximum_natural_execution_count: int | None,
        maximum_executor_step_count: int | None,
        maximum_total_token_count: int | None,
    ) -> None:
        self._identity = identity
        self._maximum_recursion_depth = maximum_recursion_depth
        self._maximum_dispatch_count = maximum_dispatch_count
        self._maximum_natural_execution_count = maximum_natural_execution_count
        self._maximum_executor_step_count = maximum_executor_step_count
        self._maximum_total_token_count = maximum_total_token_count
        self._lock = threading.Lock()
        self._dispatch_count = 0
        self._natural_execution_count = 0
        self._executor_step_count = 0
        self._total_token_count = 0

    @property
    def identity(self) -> str:
        return self._identity

    def admit(self, request: ResourceRequest) -> ResourceRejection | None:
        with self._lock:
            if request.kind == "dispatch":
                if self._maximum_recursion_depth is not None and request.recursion_depth > self._maximum_recursion_depth:
                    return ResourceRejection(reason="recursion_depth_exhausted")
                if self._maximum_dispatch_count is not None and self._dispatch_count >= self._maximum_dispatch_count:
                    return ResourceRejection(reason="dispatch_count_exhausted")
                self._dispatch_count += 1
                return None
            if self._maximum_executor_step_count is not None and self._executor_step_count >= self._maximum_executor_step_count:
                return ResourceRejection(reason="executor_step_count_exhausted")
            if self._maximum_total_token_count is not None and self._total_token_count >= self._maximum_total_token_count:
                return ResourceRejection(reason="total_token_count_exhausted")
            if request.kind == "executor_step":
                self._executor_step_count += 1
                return None
            if self._maximum_natural_execution_count is not None and self._natural_execution_count >= self._maximum_natural_execution_count:
                return ResourceRejection(reason="natural_execution_count_exhausted")
            self._natural_execution_count += 1
            return None

    def record(self, consumption: ResourceConsumption) -> None:
        with self._lock:
            self._total_token_count += consumption.executor_usage.total_token_count


def bounded_resource_allocation(
    *,
    maximum_recursion_depth: int | None = None,
    maximum_dispatch_count: int | None = None,
    maximum_natural_execution_count: int | None = None,
    maximum_executor_step_count: int | None = None,
    maximum_total_token_count: int | None = None,
    identity: str | None = None,
) -> ResourceAllocation:
    """Return a reference allocation bounded by simple counters.

    Dispatch admission counts dispatches and checks recursion depth. Natural
    execution admission counts executions and checks the executor steps and
    tokens consumed so far. Executor step admission counts one step per
    admitted request, so the step bound is enforced per executor step and
    token exhaustion surfaces at the next affected mediation seam. ``None``
    disables a bound.
    """

    return _BoundedResourceAllocation(
        identity=identity if identity is not None else generate_ulid(),
        maximum_recursion_depth=maximum_recursion_depth,
        maximum_dispatch_count=maximum_dispatch_count,
        maximum_natural_execution_count=maximum_natural_execution_count,
        maximum_executor_step_count=maximum_executor_step_count,
        maximum_total_token_count=maximum_total_token_count,
    )


@dataclass(frozen=True)
class Runner:
    """One executor and platform service configuration reusable across runs.

    No scheduler or event loop is owned here. Synchronous bodies execute on
    the calling thread; asynchronous bodies execute on the awaiting loop.
    Default execution shares the caller's interpreter with limited Python audit checks.
    External confinement is optional; configured confinement must confirm every dispatch.
    Default services cannot force cessation.

    ``perimeter_policy`` is the platform's ceiling; compositions may narrow it.
    ``resource_allocation_factory`` fixes a fresh opaque allocation at run entry.
    ``perimeter_audit_store`` must be explicitly supplied when a run permits perimeter effects.
    Choosing InMemoryPerimeterAuditStore is an explicit experimental regime, not durable auditing.
    ``accepts_aggregate_executor_accounting`` admits executors whose requests the host cannot observe individually.
    In that mode the platform must enforce per-step limits internally or accept exhaustion detected after consumption;
    the run ledger records this observation mode.
    A directly supplied ``step_executor`` serves every event loop with the same provider clients, so a synchronous
    Natural call inside a running asynchronous step requires ``from_models``, which constructs a delegate per such loop.
    """

    step_executor: StepExecutor
    perimeter_policy: PerimeterPolicy
    resource_allocation_factory: Callable[[RunIdentity], ResourceAllocation]
    perimeter_confinement: PerimeterConfinement | None = None
    perimeter_audit_store: PerimeterAuditStore | None = None
    termination_mechanism: TerminationMechanism = field(default_factory=unavailable_termination_mechanism)
    accepts_aggregate_executor_accounting: bool = False

    @classmethod
    def from_models(
        cls,
        *,
        model_configuration_tuple: tuple[nh.StepExecutorConfiguration, ...],
        default_execution_model: str,
        default_decision_model: str | None = None,
        perimeter_policy: PerimeterPolicy,
        resource_allocation_factory: Callable[[RunIdentity], ResourceAllocation],
        perimeter_confinement: PerimeterConfinement | None = None,
        perimeter_audit_store: PerimeterAuditStore | None = None,
        termination_mechanism: TerminationMechanism | None = None,
        executor_factory: Callable[[nh.StepExecutorConfiguration], nh.AgentStepExecutor] = nh.AgentStepExecutor,
    ) -> Runner:
        """Permit a fixed set of execution and decision model pairs without constructing providers.

        Each configuration is identified by its ``execution_model`` name and optional ``decision_model`` name.
        Lexical ``nh.scope(execution_model=..., decision_model=...)`` replaces either model of the current pair,
        and the resulting pair must be configured; its configured settings apply.
        A full ``step_executor_configuration`` selection must equal a configured one.
        Without a decision model, ``decide`` blocks fail. Every selection retains the same host, grant, and run allocation.
        ``executor_factory`` is trusted provider construction, invoked lazily once per selected pair
        and again only for an event loop that runs steps while every existing delegate's loop is still running.
        """

        from ._model_selection import _ModelExecutor

        return cls(
            step_executor=_ModelExecutor(model_configuration_tuple, (default_execution_model, default_decision_model), executor_factory),
            perimeter_policy=perimeter_policy,
            resource_allocation_factory=resource_allocation_factory,
            perimeter_confinement=perimeter_confinement,
            perimeter_audit_store=perimeter_audit_store,
            termination_mechanism=termination_mechanism if termination_mechanism is not None else unavailable_termination_mechanism(),
        )

    @overload
    def run[T, R](
        self,
        composition: ScaffoldComposition[T, R],
        run_input: T,
        *,
        run_id: str | None = None,
        handle: RunHandle | None = None,
    ) -> R: ...

    @overload
    def run[T, R](
        self,
        composition: AsyncScaffoldComposition[T, R],
        run_input: T,
        *,
        run_id: str | None = None,
        handle: RunHandle | None = None,
    ) -> Coroutine[Any, Any, R]: ...

    def run[T, R](
        self,
        composition: ScaffoldComposition[T, R] | AsyncScaffoldComposition[T, R],
        run_input: T,
        *,
        run_id: str | None = None,
        handle: RunHandle | None = None,
    ) -> R | Coroutine[Any, Any, R]:
        """Execute the declared call shape under the shared host contract.

        Async construction is inert until awaited. RunRejectedError precedes
        admission; RunFailedError follows terminal recording. BaseException
        propagates after cleanup. RunTerminationError means branch cessation
        remains unresolved and the ledger has no terminal event.
        """

        if isinstance(composition, AsyncScaffoldComposition):
            return _run_async(composition, run_input, runner=self, run_id=run_id, handle=handle)
        return _run_sync(composition, run_input, runner=self, run_id=run_id, handle=handle)
