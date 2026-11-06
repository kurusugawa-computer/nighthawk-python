"""Run entry and run end: the two host seams of an agent run.

An agent run is one typed Python call under its run contract. At run entry the
host admits the authority ceiling, the platform fixes the opaque resource
allocation, and both are recorded before interior execution begins. At run end
exactly one terminal candidate is validated: the scaffold composition returned
a value, terminated with an uncaught exception, or was terminated by the host.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

import nighthawk as nh
from nighthawk._identifiers import generate_ulid
from nighthawk.core.step_executor import StepExecutor
from nighthawk.harness._call_contracts import build_value_adapter, value_type_name
from nighthawk.harness._observability import emit_harness_event, harness_span
from nighthawk.harness.errors import RunFailedError, RunRejectedError, RunTerminationError
from nighthawk.harness.grants import Grant
from nighthawk.harness.ledger import CapabilityDescriptor, DefinitionMaterializationEvent, RunEntryEvent, RunLedger
from nighthawk.harness.run_contract import ApprovedFunctionDefinition, AsyncScaffoldComposition, RunIdentity, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle

if TYPE_CHECKING:
    from nighthawk.harness._host import Host
    from nighthawk.harness.runner import Runner


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

    from nighthawk.harness._natural_execution import HostStepExecutor

    host = execution.host
    uncaught: BaseException | None = None
    with harness_span("run", {"run_id": host.run_id}):
        emit_harness_event("run_entry", {"run_id": host.run_id})
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
    from nighthawk.harness._host import Host
    from nighthawk.harness._natural_execution import executor_step_observation_of

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
            result_type_name=value_type_name(accepted_value),
        )
        if not recorded:
            raise _host_termination_failure(host, event_id, None)
        return accepted_value
    finally:
        host.mark_ended()
