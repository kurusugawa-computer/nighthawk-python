"""Host-owned capability admission, typed invocation, and perimeter audit commits.

The Host supplies live execution state; these mechanisms own the capability request lifecycle.
They do not choose an agent strategy or authorize themselves from catalog metadata.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from nighthawk.harness._audit import capability_effect_scope, host_service_scope
from nighthawk.harness._call_contracts import CallContract, bind_and_validate_arguments, build_call_contract, validate_result, value_type_name
from nighthawk.harness._observability import emit_harness_event, harness_span
from nighthawk.harness.audit import PerimeterAuditRecord
from nighthawk.harness.errors import CapabilityRejectedError, PerimeterEffectRejectedError
from nighthawk.harness.grants import Capability, _is_async_callable
from nighthawk.harness.ledger import CapabilityInvocationEvent, PerimeterEffectAttemptEvent, PerimeterEffectCompletionEvent, snapshot_value

if TYPE_CHECKING:
    from nighthawk.harness._host import Host
    from nighthawk.harness.context import ExecutionContext

CAPABILITY_ATTRIBUTE_NAME = "__nighthawk_capability__"
HOST_ATTRIBUTE_NAME = "__nighthawk_host__"


@dataclass(frozen=True)
class _CapabilityMediation:
    capability: Capability
    context: ExecutionContext
    positional_arguments: tuple[object, ...]
    keyword_argument_name_to_value: dict[str, object]
    argument_snapshot: object
    attempt_event_id: str | None


def build_capability_wrapper(host: Host, capability: Capability) -> Callable[..., object]:
    function = capability.function
    call_contract = build_call_contract(function)

    if _is_async_callable(function):

        async def async_wrapper(*positional_arguments: object, **keyword_argument_name_to_value: object) -> object:
            mediation = _begin_capability_request(host, capability, call_contract, positional_arguments, keyword_argument_name_to_value)
            try:
                with (
                    capability_effect_scope(capability.effect_footprint.perimeter_effect_class_set),
                    harness_span("capability", {"capability_name": capability.name}),
                ):
                    result = await cast(Callable[..., typing.Awaitable[object]], function)(
                        *mediation.positional_arguments, **mediation.keyword_argument_name_to_value
                    )
            except BaseException as exception:
                # Record cancellation before propagating it to the joining scaffold.
                _complete_capability_request(host, mediation, call_contract, failure=exception)
                raise
            return _complete_capability_request(host, mediation, call_contract, result=result)

        wrapper: Callable[..., object] = async_wrapper
    else:

        def sync_wrapper(*positional_arguments: object, **keyword_argument_name_to_value: object) -> object:
            mediation = _begin_capability_request(host, capability, call_contract, positional_arguments, keyword_argument_name_to_value)
            try:
                with (
                    capability_effect_scope(capability.effect_footprint.perimeter_effect_class_set),
                    harness_span("capability", {"capability_name": capability.name}),
                ):
                    result = function(*mediation.positional_arguments, **mediation.keyword_argument_name_to_value)
            except BaseException as exception:
                _complete_capability_request(host, mediation, call_contract, failure=exception)
                raise
            return _complete_capability_request(host, mediation, call_contract, result=result)

        wrapper = sync_wrapper

    usage_hint = inspect.getdoc(function)
    first_line = usage_hint.splitlines()[0].strip() if usage_hint else ""
    documentation = f"[authority: {capability.authority_boundary}]"
    if first_line:
        documentation = f"{documentation} {first_line}"
    wrapper.__name__ = capability.name
    wrapper.__qualname__ = capability.name
    wrapper.__doc__ = documentation
    wrapper.__module__ = "nighthawk.harness"
    wrapper.__annotations__ = dict(getattr(function, "__annotations__", {}))
    if call_contract.signature is not None:
        wrapper.__signature__ = call_contract.signature  # type: ignore[attr-defined]
    setattr(wrapper, CAPABILITY_ATTRIBUTE_NAME, capability)
    setattr(wrapper, HOST_ATTRIBUTE_NAME, host)
    return wrapper


def _record_capability_invocation(
    host: Host,
    capability: Capability,
    node_id: str,
    status: Literal["success", "failure", "rejected"],
    *,
    argument_snapshot: object = None,
    result: object = None,
    result_type_name: str | None = None,
    failure_class_name: str | None = None,
    reason_code: str | None = None,
    parent_event_id: str | None = None,
) -> None:
    host.ledger._append(
        CapabilityInvocationEvent(
            capability_name=capability.name,
            attribution_node_id=node_id,
            status=status,
            argument_snapshot=argument_snapshot,
            result=snapshot_value(result),
            result_type_name=result_type_name,
            failure_class_name=failure_class_name,
            reason_code=reason_code,
        ),
        parent_event_id=parent_event_id if parent_event_id is not None else host.anchor_event_id(node_id),
    )
    emit_harness_event("capability_invocation", {"capability_name": capability.name, "status": status, "reason_code": reason_code})


def _begin_capability_request(
    host: Host,
    capability: Capability,
    call_contract: CallContract,
    positional_arguments: tuple[object, ...],
    keyword_argument_name_to_value: dict[str, object],
) -> _CapabilityMediation:
    if host._ended:
        raise CapabilityRejectedError(capability_name=capability.name, reason_code="run_ended")
    context = host.current_context()
    if context is None:
        raise CapabilityRejectedError(capability_name=capability.name, reason_code="no_active_run")
    node_id = context.node.node_id
    if context.grant.capabilities.get(capability.name) is not capability:
        _record_capability_invocation(host, capability, node_id, "rejected", reason_code="not_in_current_grant")
        raise CapabilityRejectedError(capability_name=capability.name, reason_code="not_in_current_grant")
    if host._terminated:
        raise CapabilityRejectedError(capability_name=capability.name, reason_code="run_terminated")
    if host.is_node_terminated(node_id):
        _record_capability_invocation(host, capability, node_id, "rejected", reason_code="node_terminated")
        raise CapabilityRejectedError(capability_name=capability.name, reason_code="node_terminated")
    if not host.is_node_live(context.node):
        _record_capability_invocation(host, capability, node_id, "rejected", reason_code="node_ended")
        raise CapabilityRejectedError(capability_name=capability.name, reason_code="node_ended")
    try:
        validated_positional, validated_keyword_argument_name_to_value = bind_and_validate_arguments(
            call_contract, positional_arguments, keyword_argument_name_to_value
        )
    except ValueError as error:
        _record_capability_invocation(host, capability, node_id, "rejected", reason_code="request_type_invalid")
        raise CapabilityRejectedError(capability_name=capability.name, reason_code="request_type_invalid", detail=str(error)) from error

    attempt_event_id: str | None = None
    perimeter_classes = capability.effect_footprint.perimeter_effect_class_set
    if perimeter_classes:
        if not host.effective_perimeter_policy.permits(perimeter_classes):
            _record_capability_invocation(host, capability, node_id, "rejected", reason_code="outside_effective_authority")
            raise PerimeterEffectRejectedError(capability_name=capability.name, reason="outside_effective_authority", attribution_node_id=node_id)
        attempt_event = PerimeterEffectAttemptEvent(
            capability_name=capability.name,
            attribution_node_id=node_id,
            perimeter_effect_class_tuple=tuple(sorted(perimeter_classes)),
        )
        attempt_event_id = host.ledger.reserve_event_id()
        try:
            _store_perimeter_record(
                host,
                PerimeterAuditRecord(
                    run_id=host.run_id, event_id=attempt_event_id, parent_event_id=host.anchor_event_id(node_id), event=attempt_event
                ),
            )
        except Exception as error:
            _record_capability_invocation(host, capability, node_id, "rejected", reason_code="attempt_record_failed")
            raise PerimeterEffectRejectedError(
                capability_name=capability.name, reason="attempt_record_failed", attribution_node_id=node_id
            ) from error
        host.ledger._append(attempt_event, event_id=attempt_event_id, parent_event_id=host.anchor_event_id(node_id))
    return _CapabilityMediation(
        capability=capability,
        context=context,
        positional_arguments=validated_positional,
        keyword_argument_name_to_value=validated_keyword_argument_name_to_value,
        argument_snapshot=snapshot_value({"positional": validated_positional, "keyword": validated_keyword_argument_name_to_value}),
        attempt_event_id=attempt_event_id,
    )


def _record_perimeter_completion(
    host: Host,
    mediation: _CapabilityMediation,
    status: Literal["success", "failure"],
    *,
    failure_class_name: str | None,
) -> str | None:
    if mediation.attempt_event_id is None:
        return None
    completion_event = PerimeterEffectCompletionEvent(
        attempt_event_id=mediation.attempt_event_id,
        capability_name=mediation.capability.name,
        status=status,
        failure_class_name=failure_class_name,
    )
    completion_event_id = host.ledger.reserve_event_id()
    try:
        _store_perimeter_record(
            host,
            PerimeterAuditRecord(
                run_id=host.run_id,
                event_id=completion_event_id,
                parent_event_id=mediation.attempt_event_id,
                event=completion_event,
            ),
        )
    except Exception:  # noqa: BLE001 - Preserve observed effects when the audit service fails.
        # The effect already happened. Without a durable completion record its
        # completion status is authoritatively indeterminate; the run ledger
        # still keeps the host's observation.
        emit_harness_event("perimeter_completion_record_failed", {"capability_name": mediation.capability.name})
    host.ledger._append(completion_event, parent_event_id=mediation.attempt_event_id, event_id=completion_event_id)
    return completion_event_id


def _store_perimeter_record(host: Host, record: PerimeterAuditRecord) -> None:
    with host_service_scope():
        store = host.runner.perimeter_audit_store
        assert store is not None
        store.record(record)


def _complete_capability_request(
    host: Host,
    mediation: _CapabilityMediation,
    call_contract: CallContract,
    *,
    result: object = None,
    failure: BaseException | None = None,
) -> object:
    capability = mediation.capability
    node_id = mediation.context.node.node_id
    if failure is not None:
        completion_event_id = _record_perimeter_completion(host, mediation, "failure", failure_class_name=type(failure).__name__)
        _record_capability_invocation(
            host,
            capability,
            node_id,
            "failure",
            argument_snapshot=mediation.argument_snapshot,
            failure_class_name=type(failure).__name__,
            parent_event_id=completion_event_id,
        )
        return None
    completion_event_id = _record_perimeter_completion(host, mediation, "success", failure_class_name=None)
    try:
        validated_result = validate_result(call_contract, result)
    except ValueError as error:
        _record_capability_invocation(
            host,
            capability,
            node_id,
            "rejected",
            argument_snapshot=mediation.argument_snapshot,
            reason_code="result_type_invalid",
            parent_event_id=completion_event_id,
        )
        raise CapabilityRejectedError(capability_name=capability.name, reason_code="result_type_invalid") from error
    _record_capability_invocation(
        host,
        capability,
        node_id,
        "success",
        argument_snapshot=mediation.argument_snapshot,
        result=validated_result,
        result_type_name=value_type_name(validated_result),
        parent_event_id=completion_event_id,
    )
    return validated_result
