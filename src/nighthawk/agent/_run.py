"""Run entry and run end: the two host seams of an agent run.

An agent run is one typed Python call under its run contract. At run entry the
host admits the authority ceiling, the platform fixes the opaque resource
allocation, and both are recorded before interior execution begins. At run end
exactly one terminal candidate is validated: the scaffold composition returned
a value, terminated with an uncaught exception, or was terminated by the host.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import threading
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

import nighthawk as nh
from nighthawk.runtime.step_executor import StepExecutor

from ._call_contracts import build_value_adapter
from ._grants import Grant
from ._ledger import CapabilityDescriptor, DefinitionMaterializationEvent, RunEntryEvent, RunLedger, generate_ulid
from ._observability import agent_span, emit_agent_event
from ._perimeter import PerimeterPolicy
from .errors import RunFailedError, RunRejectedError, RunTerminationError

if TYPE_CHECKING:
    from ._host import Host
    from .runtime import Runner

__all__ = [
    "ApprovedFunctionDefinition",
    "AsyncScaffoldComposition",
    "RunContract",
    "RunHandle",
    "ScaffoldComposition",
]


@dataclass(frozen=True)
class RunContract[T, R]:
    """The typed Python call boundary of one agent run.

    Ordinary application classes use identity-preserving instance checks.
    Annotated constraints and types with their own validation retain that validation;
    instance checks do not validate an arbitrary object's internal state.
    """

    input_type: type[T]
    result_type: type[R]


@dataclass(frozen=True)
class ApprovedFunctionDefinition:
    """Host-supplied association with an existing independent approval decision.

    This records approval evidence supplied at run entry; constructing it does
    not approve source. The application owns the decision and the exact
    source/callable correspondence. An omitted wrapper uses ordinary repository
    provenance for the callable. Neither route grants invocation authority.
    """

    function: Callable[..., object]
    source_identity: str
    approval_identity: str
    approval_route: str
    originating_cell_id: str | None = None


@dataclass(frozen=True)
class ScaffoldComposition[T, R]:
    """A deterministic Python composition that configures and invokes the substrate.

    ``body`` is the run's control flow. ``run_entry_grant`` and
    ``composition_perimeter_policy`` form the authority ceiling admitted at run
    entry. ``function_definition_tuple`` lists the initial ratified function
    definitions recorded at the run root.
    """

    run_contract: RunContract[T, R]
    body: Callable[[T], R]
    run_entry_grant: Grant = field(default_factory=lambda: Grant.EMPTY)
    composition_perimeter_policy: PerimeterPolicy = PerimeterPolicy.NONE
    function_definition_tuple: tuple[Callable[..., object] | ApprovedFunctionDefinition, ...] = ()


@dataclass(frozen=True)
class AsyncScaffoldComposition[T, R]:
    """An awaited composition whose contract describes its completed result.

    The run contract describes the completed result, while body describes the
    awaitable that produces it. The separate shape makes run dispatch typed.
    """

    run_contract: RunContract[T, R]
    body: Callable[[T], Awaitable[R]]
    run_entry_grant: Grant = field(default_factory=lambda: Grant.EMPTY)
    composition_perimeter_policy: PerimeterPolicy = PerimeterPolicy.NONE
    function_definition_tuple: tuple[Callable[..., object] | ApprovedFunctionDefinition, ...] = ()


class RunHandle:
    """Platform-side handle to one run: its ledger and host-directed termination."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._host: Host | None = None
        self._ledger: RunLedger | None = None
        self._termination_requested = False
        self._termination_reason: str | None = None

    def _attach(self, host: Host) -> None:
        with self._lock:
            self._host = host
            self._ledger = host.ledger
            termination_requested = self._termination_requested
            termination_reason = self._termination_reason
        if termination_requested:
            host.terminate_run(termination_reason or "host directed")

    @property
    def ledger(self) -> RunLedger:
        with self._lock:
            if self._ledger is None:
                raise RuntimeError("the run has not started")
            return self._ledger

    @property
    def run_id(self) -> str | None:
        with self._lock:
            return self._host.run_id if self._host is not None else None

    def terminate(self, reason: str = "host directed") -> bool:
        """Direct the host to terminate the run and return whether cessation was established.

        Every active interior branch, including the scaffold composition's own
        execution while it is running, is presented to the platform termination
        mechanism. ``True`` means the platform established that no interior
        branch can execute or submit operations any more and the host recorded
        the run end; the pending ``Runner.run`` call then raises a typed run
        failure. ``False`` means the platform did not establish cessation: no
        run-end event exists, the host refuses every further interior operation
        at its mediation seams, and the run ends only when the scaffold reaches
        the run-end seam on its own. A request made before the run starts ends
        the run before any interior execution begins.
        """

        with self._lock:
            self._termination_requested = True
            self._termination_reason = reason
            host = self._host
        if host is None:
            return True
        return host.terminate_run(reason)

    def _finish_pending(self) -> None:
        """Finish only cleanup after the body unwound with unresolved cessation.

        This never drives interior execution. The owner must keep its original
        driver until the body has unwound; a Session may then reattach here.
        """

        with self._lock:
            host = self._host
        if host is None or host.run_root_active or not host.ended:
            raise RuntimeError("cleanup requires an unwound run body")
        if not host.terminate_run("finish pending run cleanup"):
            remaining = host.nonterminal_dispatch_node_id_tuple()
            event_id = host.record_anomaly(
                "branch_cessation_unestablished",
                node_id=host.run_root.node_id,
                detail=f"cleanup still awaits cessation of: {', '.join(remaining)}",
            )
            raise RunTerminationError(run_id=host.run_id, anomaly_event_id=event_id, nonterminal_node_id_tuple=remaining)

    def _pending_branch_loop_tuple(self) -> tuple[asyncio.AbstractEventLoop, ...]:
        with self._lock:
            host = self._host
        return () if host is None else host.pending_branch_loop_tuple()


def _type_name(value_type: object) -> str:
    module_name = getattr(value_type, "__module__", None) or "<unknown>"
    qualified_name = getattr(value_type, "__qualname__", None) or getattr(value_type, "__name__", None) or repr(value_type)
    return f"{module_name}.{qualified_name}"


def _definition_identity(function: Callable[..., object]) -> str:
    module_name = getattr(function, "__module__", None) or "<unknown>"
    qualified_name = getattr(function, "__qualname__", None) or getattr(function, "__name__", None) or repr(function)
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        return f"{module_name}.{qualified_name}"
    return f"{module_name}.{qualified_name}:{hashlib.sha256(source.encode('utf-8')).hexdigest()[:16]}"


def _definition_event(definition: Callable[..., object] | ApprovedFunctionDefinition, origin_node_id: str) -> DefinitionMaterializationEvent:
    approval = definition if isinstance(definition, ApprovedFunctionDefinition) else None
    function = approval.function if approval is not None else cast(Callable[..., object], definition)
    return DefinitionMaterializationEvent(
        definition_id=generate_ulid(),
        export_name=getattr(function, "__name__", type(function).__name__),
        definition_kind="async_function" if inspect.iscoroutinefunction(function) else "function",
        source_provenance_class="ratified",
        defining_source_identity=_definition_identity(function) if approval is None else approval.source_identity,
        materialization_origin_node_id=origin_node_id,
        approval_identity=None if approval is None else approval.approval_identity,
        approval_route=None if approval is None else approval.approval_route,
        originating_cell_id=None if approval is None else approval.originating_cell_id,
    )


def _run_sync[T, R](
    composition: ScaffoldComposition[T, R],
    run_input: T,
    *,
    runner: Runner,
    run_id: str | None = None,
    handle: RunHandle | None = None,
) -> R:
    """Execute one agent run of ``composition`` and return its typed result.

    The scaffold composition receives the run input as validated against the
    declared input type, and the returned value is the run result as validated
    against the declared result type; when a type coerces, the coerced value
    is what the composition receives, what is returned, and what is recorded.

    This is the synchronous specialization. Ordinary BaseException interrupts
    propagate after cleanup and the terminal ledger decision; the private
    asynchronous specialization shares these same admission and end seams.

    Raises:
        RunRejectedError: when a run-entry declaration cannot be admitted (for
            example an executor whose steps the host cannot observe while the
            platform has not accepted aggregate accounting); the run does not
            start and no ledger exists.
        RunFailedError: when the run ends without an accepted typed result.
        RunTerminationError: when interior branches were active at run end and
            the platform did not establish their cessation.
    """

    execution = _admit_run(composition, run_input, runner=runner, run_id=run_id, handle=handle)
    with _run_execution(execution):
        if not execution.host.terminated:
            execution.value = composition.body(execution.admitted_input)
    return cast(R, execution.value)


async def _run_async[T, R](
    composition: AsyncScaffoldComposition[T, R],
    run_input: T,
    *,
    runner: Runner,
    run_id: str | None = None,
    handle: RunHandle | None = None,
) -> R:
    """Awaited specialization over the same admission and finalization.

    Construction does not admit a run. Awaiting starts it in the caller's loop;
    the host context remains active across every suspension.
    Runner selects this path from the nominal async composition shape.
    """

    execution = _admit_run(composition, run_input, runner=runner, run_id=run_id, handle=handle)
    with _run_execution(execution):
        if not execution.host.terminated:
            execution.value = await composition.body(execution.admitted_input)
    return cast(R, execution.value)


@dataclass
class _AdmittedRun[T]:
    host: Host
    admitted_input: T
    result_adapter: TypeAdapter[Any]
    grant: Grant
    step_executor: StepExecutor
    value: object = None


@contextmanager
def _run_execution(execution: _AdmittedRun[Any]) -> Generator[None]:
    """Keep the host active through the body and finalize its terminal candidate once."""

    from ._natural_execution import HostStepExecutor

    host = execution.host
    uncaught: BaseException | None = None
    with agent_span("run", {"run_id": host.run_id}):
        emit_agent_event("run_entry", {"run_id": host.run_id})
        try:
            with nh.run(HostStepExecutor(host, execution.step_executor), run_id=host.run_id), host.run_scope(execution.grant):
                yield
        except BaseException as exception:  # noqa: BLE001 - Finalize the run before propagating failure or interruption.
            uncaught = exception
        try:
            execution.value = _run_end_seam(host, execution.result_adapter, execution.value, uncaught)
        except RunFailedError:
            if uncaught is not None and not isinstance(uncaught, Exception):
                raise uncaught from None
            raise


def _admit_run[T, R](
    composition: ScaffoldComposition[T, R] | AsyncScaffoldComposition[T, R],
    run_input: T,
    *,
    runner: Runner,
    run_id: str | None,
    handle: RunHandle | None,
) -> _AdmittedRun[T]:
    from ._host import Host
    from ._natural_execution import executor_step_observation_of
    from .runtime import RunIdentity

    step_executor = runner.step_executor
    platform_policy = runner.perimeter_policy
    excess = composition.composition_perimeter_policy.excess_over(platform_policy)
    if excess:
        raise RunRejectedError(reason="perimeter_policy_widening", detail=", ".join(sorted(excess)))
    effective_policy = composition.composition_perimeter_policy.intersection(platform_policy)
    if effective_policy.permitted_effect_class_set and runner.perimeter_audit_store is None:
        raise RunRejectedError(reason="invalid_declaration", detail="perimeter effects require an explicitly configured audit store")
    for name, capability in composition.run_entry_grant.capabilities.items():
        if not effective_policy.permits(capability.effect_footprint.perimeter_effect_class_set):
            raise RunRejectedError(reason="capability_outside_perimeter_policy", detail=name)
    contract = composition.run_contract
    try:
        input_adapter = build_value_adapter(contract.input_type)
        admitted_input = cast(T, input_adapter.validate_python(run_input))
    except Exception as error:
        raise RunRejectedError(reason="input_type_invalid", detail=str(error)) from error
    try:
        result_adapter = build_value_adapter(contract.result_type)
    except Exception as error:
        raise RunRejectedError(reason="invalid_declaration", detail="result type is not validatable") from error
    executor_step_observation = executor_step_observation_of(step_executor)
    if executor_step_observation == "aggregate" and not runner.accepts_aggregate_executor_accounting:
        raise RunRejectedError(reason="executor_steps_unobservable", detail=type(step_executor).__name__)

    resolved_run_id = run_id if run_id is not None else generate_ulid()
    ledger = RunLedger(resolved_run_id)
    resource_allocation = runner.resource_allocation_factory(RunIdentity(run_id=resolved_run_id))
    host = Host(
        run_id=resolved_run_id,
        runner=runner,
        ledger=ledger,
        effective_perimeter_policy=effective_policy,
        resource_allocation=resource_allocation,
        function_definition_tuple=tuple(
            definition.function if isinstance(definition, ApprovedFunctionDefinition) else definition
            for definition in composition.function_definition_tuple
        ),
    )
    host.executor_step_observation = executor_step_observation
    run_entry = ledger._append(
        RunEntryEvent(
            run_id=resolved_run_id,
            input_type_name=_type_name(contract.input_type),
            result_type_name=_type_name(contract.result_type),
            capability_descriptor_tuple=tuple(
                CapabilityDescriptor(
                    name=name,
                    declared_type=capability.declared_type,
                    authority_boundary=capability.authority_boundary,
                    effect_footprint=capability.effect_footprint,
                )
                for name, capability in sorted(composition.run_entry_grant.capabilities.items())
            ),
            reference_name_tuple=tuple(sorted(composition.run_entry_grant.references)),
            composition_perimeter_policy=composition.composition_perimeter_policy,
            platform_perimeter_policy=platform_policy,
            effective_perimeter_policy=effective_policy,
            resource_allocation_identity=resource_allocation.identity,
            executor_step_observation=executor_step_observation,
        )
    )
    host.set_anchor_event_id(host.run_root.node_id, run_entry.event_id)
    for function in composition.function_definition_tuple:
        ledger._append(
            _definition_event(function, host.run_root.node_id),
            parent_event_id=run_entry.event_id,
        )
    if handle is not None:
        handle._attach(host)

    return _AdmittedRun(host, admitted_input, result_adapter, composition.run_entry_grant, step_executor)


def _host_termination_failure(host: Host, event_id: str, uncaught: BaseException | None) -> RunFailedError:
    failure = RunFailedError(
        run_id=host.run_id,
        terminal_event_id=event_id,
        terminal_kind="host_termination",
        message="host-directed termination",
    )
    failure.__cause__ = uncaught
    return failure


def _run_end_seam(host: Host, result_adapter: TypeAdapter[Any], value: object, uncaught: BaseException | None) -> object:
    """Decide the single terminal candidate of the run and record the run-end event once.

    The run ledger decides: every attempt to record a run-end event reports
    whether it was the one recorded, and when host-directed termination
    recorded the run end first (before or during this seam), the seam raises
    that typed run failure instead of returning a value.
    """

    try:
        if host.run_end_event_id is not None:
            raise _host_termination_failure(host, host.run_end_event_id, uncaught)
        host.fail_pending_natural_executions()
        straggler_tuple = host.nonterminal_dispatch_node_id_tuple()
        if straggler_tuple and not host.terminated:
            host.record_anomaly(
                "interior_branch_active_at_run_end",
                node_id=host.run_root.node_id,
                detail=f"nonterminal dispatch nodes: {', '.join(straggler_tuple)}",
            )
        if straggler_tuple:
            host.retain_pending_run_failure(uncaught)
            ceased = host.terminate_all_branches(detail="interior branches active at run end", include_run_root=False)
            if not ceased:
                remaining_tuple = host.nonterminal_dispatch_node_id_tuple()
                anomaly_event_id = host.record_anomaly(
                    "branch_cessation_unestablished",
                    node_id=host.run_root.node_id,
                    detail=f"the platform did not establish cessation of: {', '.join(remaining_tuple)}",
                )
                raise RunTerminationError(
                    run_id=host.run_id, anomaly_event_id=anomaly_event_id, nonterminal_node_id_tuple=remaining_tuple
                ) from uncaught
        if host.terminated or straggler_tuple:
            detail = "host-directed termination" if host.terminated else "interior branches were active at run end"
            event_id, _recorded = host.record_run_end("host_termination", detail=detail)
            raise RunFailedError(run_id=host.run_id, terminal_event_id=event_id, terminal_kind="host_termination", message=detail) from uncaught
        if uncaught is not None:
            event_id, recorded = host.record_run_end("uncaught_exception", failure_class_name=type(uncaught).__name__)
            if not recorded:
                raise _host_termination_failure(host, event_id, uncaught)
            raise RunFailedError(
                run_id=host.run_id,
                terminal_event_id=event_id,
                terminal_kind="uncaught_exception",
                message=type(uncaught).__name__,
            ) from uncaught
        try:
            accepted_value = result_adapter.validate_python(value)
        except Exception as error:
            event_id, recorded = host.record_run_end("validation_rejection", failure_class_name=type(error).__name__)
            if not recorded:
                raise _host_termination_failure(host, event_id, error) from error
            raise RunFailedError(
                run_id=host.run_id,
                terminal_event_id=event_id,
                terminal_kind="validation_rejection",
                message="the returned value does not satisfy the declared result type",
            ) from error
        event_id, recorded = host.record_run_end(
            "accepted",
            result=accepted_value,
            result_type_name=f"{type(accepted_value).__module__}.{type(accepted_value).__qualname__}",
        )
        if not recorded:
            raise _host_termination_failure(host, event_id, None)
        return accepted_value
    finally:
        host.mark_ended()
