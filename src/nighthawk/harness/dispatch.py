"""The sanctioned recursion channel: host-owned dispatch of code proposals.

Generated source reaches execution only through :func:`dispatch` (for ratified
interior execution), the ``execute_code`` capability (for Natural and generated
execution), or invocation of a :class:`GeneratedCallableReference`. Every entry
validates the request, the current grant, the effect footprint, platform
configured confinement, and resource admission, records the dispatch before execution, and
appends exactly one dispatch-outcome event when the node terminates.

Generated source is compiled from its instrumented form: every function body,
lambda, generator expression, and resumption point calls a host guard bound in
the generated namespace, so a private helper, closure, method, generator, or
coroutine that escapes its dispatch cannot start or resume outside a live
generated execution of that source. After materialization every declared
function export is rebound in the generated namespace to its host wrapper, so a
generated function that calls or returns a sibling export goes through the same
dispatch as any other caller.
"""

from __future__ import annotations

import builtins
import inspect
import linecache
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import ValidationError

from nighthawk._identifiers import generate_ulid
from nighthawk.core.annotations import module_annotations
from nighthawk.harness._audit import host_service_scope
from nighthawk.harness._call_contracts import (
    CallContract,
    bind_and_validate_arguments,
    build_call_contract,
    build_value_adapter,
    validate_result,
    value_type_name,
)
from nighthawk.harness._execution_guards import guard_name_to_value
from nighthawk.harness._host import GENERATED_MODULE_NAME_PREFIX, Host
from nighthawk.harness._observability import emit_harness_event, harness_span
from nighthawk.harness._source import SourceAnalysis, SourceRejectedError, analyze_generated_source
from nighthawk.harness.confinement import ConfinementRequest
from nighthawk.harness.context import AttributionNode, ExecutionContext, current_host
from nighthawk.harness.errors import DispatchRejectedError, DispatchRejectionReason, GeneratedExecutionError
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import DefinitionKind, DefinitionMaterializationEvent, DispatchEvent, DispatchKind, snapshot_value
from nighthawk.harness.perimeter import EffectFootprint, PerimeterEffectClass
from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration

if typing.TYPE_CHECKING:
    from nighthawk.harness.generated import RetainedFunction

__all__ = [
    "AsyncGeneratedCallableReference",
    "DispatchResult",
    "GeneratedCallableReference",
    "GeneratedDefinition",
    "dispatch",
    "execute_code_capability",
]


@dataclass(frozen=True)
class GeneratedDefinition:
    """Run-scoped identity of one materialized generated definition."""

    definition_id: str
    export_name: str
    definition_kind: DefinitionKind
    defining_source_identity: str
    dispatch_node_id: str
    materialization_event_id: str


@dataclass(frozen=True)
class DispatchResult:
    """Typed result of one code-proposal dispatch.

    ``export_name_to_value`` maps each declared export to its generated callable
    reference (function exports) or committed value (value exports).
    ``entry_result`` carries the entry export's validated return value for
    definition-and-invocation dispatches and is ``None`` otherwise;
    ``entry_dispatch_node_id`` then identifies the separate invocation node
    that produced it.
    """

    dispatch_node_id: str
    definition_tuple: tuple[GeneratedDefinition, ...]
    export_name_to_value: Mapping[str, object]
    entry_result: object = None
    entry_dispatch_node_id: str | None = None

    @property
    def reference_tuple(self) -> tuple[GeneratedCallableReference, ...]:
        return tuple(value for value in self.export_name_to_value.values() if isinstance(value, GeneratedCallableReference))


@dataclass(frozen=True)
class _ResolvedDependencies:
    capability_tuple: tuple[Capability, ...]
    name_to_reference: dict[str, object]
    name_to_binding: dict[str, object]


@dataclass
class _MaterializedSource:
    """Shared state of every export materialized from one code proposal.

    ``namespace`` is the module namespace of the generated source, in which
    function exports are rebound to their host wrappers and the execution guard
    bindings are bound. Generated Natural functions share this namespace as
    their module globals, so rebinding here reaches them too.
    """

    namespace: dict[str, object]
    guard_name_to_value: dict[str, object]

    def bind(self, name_to_value: Mapping[str, object]) -> None:
        self.namespace.update(name_to_value)

    def guards_intact(self) -> bool:
        """Whether the namespace still binds the host guard bindings under their reserved names."""

        return all(self.namespace.get(name) is value for name, value in self.guard_name_to_value.items())


class GeneratedCallableReference:
    """Ordinary typed Python reference to one materialized generated function.

    Each invocation enters generated execution through host-owned dispatch: the
    host resolves the declared dependencies from the current grant by name and
    declared type, validates the arguments against the definition's call
    contract, admits the dispatch, runs the body under the derived invocation
    grant, validates the result, and records the dispatch outcome with the
    committed result. The reference carries definition identity but no
    authority.

    Explicit annotations, including Annotated constraints, are validated rather than silently discarded.
    Ordinary application classes use identity-preserving instance checks; object, Any, and missing annotations remain unconstrained.
    """

    def __init__(
        self,
        *,
        host: Host,
        definition: GeneratedDefinition,
        function: types.FunctionType,
        dependency_tuple: tuple[DependencyDeclaration, ...],
        effect_footprint: EffectFootprint,
        source: _MaterializedSource,
    ) -> None:
        self._host = host
        self._definition = definition
        self._function = function
        self._code = function.__code__
        self._dependency_tuple = dependency_tuple
        self._effect_footprint = effect_footprint
        self._source = source
        self._call_contract = build_call_contract(function)
        self.__name__ = definition.export_name
        self.__qualname__ = definition.export_name
        self.__doc__ = function.__doc__
        self.__module__ = "nighthawk.harness"
        self.__annotations__ = dict(getattr(function, "__annotations__", {}))
        if self._call_contract.signature is not None:
            self.__signature__ = self._call_contract.signature

    @property
    def definition(self) -> GeneratedDefinition:
        return self._definition

    @property
    def effect_footprint(self) -> EffectFootprint:
        return self._effect_footprint

    @property
    def dependency_tuple(self) -> tuple[DependencyDeclaration, ...]:
        return self._dependency_tuple

    @property
    def is_async(self) -> bool:
        return False

    def __nighthawk_ledger_snapshot__(self) -> GeneratedDefinition:
        """Audit representation: the stable definition identity, never the live reference."""

        return self._definition

    def __call__(self, *positional_arguments: object, **keyword_arguments: object) -> object:
        plan = _prepare_invocation(self, positional_arguments, keyword_arguments)
        result = _run_generated_body(plan)
        plan.host.record_dispatch_outcome(plan.node, "success", result=result, result_type_name=value_type_name(result))
        return result

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._definition.export_name!r}, definition_id={self._definition.definition_id!r})"


class AsyncGeneratedCallableReference(GeneratedCallableReference):
    """Reference to a materialized asynchronous generated function.

    Calling it returns a coroutine; awaiting the coroutine performs the whole
    host mediation (validation, admission, dispatch record, body execution,
    result validation, outcome record). A coroutine that is never awaited
    records no dispatch.
    """

    @property
    def is_async(self) -> bool:
        return True

    async def __call__(self, *positional_arguments: object, **keyword_arguments: object) -> object:  # type: ignore[override]
        plan = _prepare_invocation(self, positional_arguments, keyword_arguments)
        plan.host.register_branch_loop(plan.node)
        result = await _run_generated_body_async(plan)
        plan.host.record_dispatch_outcome(plan.node, "success", result=result, result_type_name=value_type_name(result))
        return result


@dataclass(frozen=True)
class _InvocationPlan:
    host: Host
    node: AttributionNode
    invocation_grant: Grant
    function: Callable[..., object]
    call_contract: CallContract
    source: _MaterializedSource
    positional_arguments: tuple[object, ...]
    keyword_arguments: dict[str, object]


def _reject(
    reason: DispatchRejectionReason,
    *,
    context: ExecutionContext | None,
    detail: str | None = None,
    rejection_event_id: str | None = None,
) -> DispatchRejectedError:
    emit_harness_event("dispatch_rejected", {"reason": reason})
    return DispatchRejectedError(
        reason=reason,
        detail=detail,
        attribution_node_id=context.node.node_id if context is not None else None,
        rejection_event_id=rejection_event_id,
    )


def _require_live_context(host: Host | None) -> tuple[Host, ExecutionContext]:
    if host is None:
        raise _reject("no_active_run", context=None)
    if host.ended:
        raise _reject("run_ended", context=None)
    context = host.current_context()
    if context is None:
        raise _reject("no_active_run", context=None)
    if host.terminated:
        raise _reject("run_terminated", context=context)
    if host.is_node_terminated(context.node.node_id):
        raise _reject("node_terminated", context=context)
    if not host.is_node_live(context.node):
        raise _reject("node_ended", context=context)
    return host, context


def _validate_reference_dependency_type(context: ExecutionContext, dependency: DependencyDeclaration, value: object) -> None:
    try:
        adapter = build_value_adapter(dependency.declared_type)
    except Exception as error:
        raise _reject("dependency_type_invalid", context=context, detail=dependency.name) from error
    try:
        adapter.validate_python(value, strict=True)
    except Exception as error:
        raise _reject("dependency_type_mismatch", context=context, detail=dependency.name) from error


def _resolve_dependencies(
    host: Host,
    context: ExecutionContext,
    dependency_tuple: tuple[DependencyDeclaration, ...],
    effect_footprint: EffectFootprint,
) -> _ResolvedDependencies:
    """Resolve each declared dependency from the current grant by name, kind, and declared type."""

    grant = context.grant
    capability_list: list[Capability] = []
    name_to_reference: dict[str, object] = {}
    name_to_binding: dict[str, object] = {}
    for dependency in dependency_tuple:
        actual_kind = grant.entry_kind(dependency.name)
        if actual_kind is None:
            raise _reject("dependency_missing", context=context, detail=dependency.name)
        if actual_kind != dependency.kind:
            raise _reject("dependency_kind_mismatch", context=context, detail=dependency.name)
        if dependency.kind == "capability":
            capability = grant.capabilities[dependency.name]
            if capability.declared_type != dependency.declared_type:
                raise _reject("dependency_type_mismatch", context=context, detail=dependency.name)
            if dependency.name not in effect_footprint.capability_name_set:
                raise _reject("footprint_capability_undeclared", context=context, detail=dependency.name)
            capability_list.append(capability)
            name_to_binding[dependency.name] = host.capability_wrapper(capability)
        else:
            value = grant.references[dependency.name]
            _validate_reference_dependency_type(context, dependency, value)
            name_to_reference[dependency.name] = value
            name_to_binding[dependency.name] = value
    return _ResolvedDependencies(
        capability_tuple=tuple(capability_list),
        name_to_reference=name_to_reference,
        name_to_binding=name_to_binding,
    )


def _validate_footprint(
    context: ExecutionContext,
    effect_footprint: EffectFootprint,
    dependency_tuple: tuple[DependencyDeclaration, ...],
    effective_perimeter_authority: frozenset[PerimeterEffectClass],
) -> None:
    grant = context.grant
    for capability_name in sorted(effect_footprint.capability_name_set):
        if capability_name not in grant.capabilities:
            raise _reject("capability_not_granted", context=context, detail=capability_name)
    reference_dependency_name_set = {dependency.name for dependency in dependency_tuple if dependency.kind == "reference"}
    undeclared_mutable = effect_footprint.mutable_reference_name_set - reference_dependency_name_set
    if undeclared_mutable:
        raise _reject("footprint_mutable_reference_undeclared", context=context, detail=", ".join(sorted(undeclared_mutable)))
    reachable_perimeter: set[PerimeterEffectClass] = set()
    for capability_name in effect_footprint.capability_name_set:
        reachable_perimeter.update(grant.capabilities[capability_name].effect_footprint.perimeter_effect_class_set)
    undeclared_perimeter = reachable_perimeter - effect_footprint.perimeter_effect_class_set
    if undeclared_perimeter:
        raise _reject("footprint_perimeter_undeclared", context=context, detail=", ".join(sorted(undeclared_perimeter)))
    outside_authority = effect_footprint.perimeter_effect_class_set - effective_perimeter_authority
    if outside_authority:
        raise _reject("perimeter_outside_authority", context=context, detail=", ".join(sorted(outside_authority)))


def _effective_perimeter_authority(host: Host, grant: Grant) -> frozenset[PerimeterEffectClass]:
    return host.effective_perimeter_policy.permitted_effect_class_set & grant.perimeter_effect_class_set


def _argument_carried_capabilities(
    host: Host,
    context: ExecutionContext,
    effect_footprint: EffectFootprint,
    positional_arguments: tuple[object, ...],
    keyword_arguments: Mapping[str, object],
) -> tuple[Capability, ...]:
    carried: list[Capability] = []
    for value in (*positional_arguments, *keyword_arguments.values()):
        if isinstance(value, Capability):
            raise _reject("argument_capability_unauthorized", context=context, detail=value.name)
        capability = Host.capability_of(value)
        if capability is None:
            continue
        if Host.host_of_wrapper(value) is not host or context.grant.capabilities.get(capability.name) is not capability:
            raise _reject("argument_capability_unauthorized", context=context, detail=capability.name)
        if capability.name not in effect_footprint.capability_name_set:
            raise _reject("argument_capability_undeclared", context=context, detail=capability.name)
        carried.append(capability)
    return tuple(carried)


def _confirm_confinement(host: Host, context: ExecutionContext, effect_footprint: EffectFootprint) -> str | None:
    if host.runner.perimeter_confinement is None:
        return None
    with host_service_scope():
        confirmation = host.runner.perimeter_confinement.confirm(
            ConfinementRequest(
                run_id=host.run_id,
                submitting_node_id=context.node.node_id,
                recursion_depth=context.node.recursion_depth + 1,
                effect_footprint=effect_footprint,
            )
        )
    if confirmation is None:
        raise _reject("confinement_unavailable", context=context)
    return confirmation.confinement_identity


def _admit_dispatch(host: Host, context: ExecutionContext) -> AttributionNode:
    prospective = AttributionNode(
        node_id="",
        parent_node_id=context.node.node_id,
        recursion_depth=context.node.recursion_depth + 1,
        kind="dispatch",
    )
    rejection = host.admit_resource("dispatch", prospective)
    if rejection is not None:
        exhaustion_event_id = host.record_exhaustion("dispatch", prospective, rejection)
        raise _reject("allocation_exhausted", context=context, detail=rejection.reason, rejection_event_id=exhaustion_event_id)
    node = host.create_dispatch_node(context.node)
    host.record_consumption("dispatch", node)
    return node


def _materialize(
    host: Host,
    node: AttributionNode,
    proposal: CodeProposal,
    analysis: SourceAnalysis,
    resolved: _ResolvedDependencies,
    *,
    filename: str,
    dispatch_event_id: str,
    retained_definition_id: str | None = None,
    retained_export_name: str | None = None,
) -> tuple[tuple[GeneratedDefinition, ...], dict[str, object]]:
    export_name_to_kind = analysis.export_name_to_kind
    namespace: dict[str, object] = {
        "__name__": f"{GENERATED_MODULE_NAME_PREFIX}{node.node_id}",
        "__doc__": None,
        "__builtins__": builtins,
    }
    source_holder: list[_MaterializedSource] = []

    def guard() -> None:
        host.require_generated_execution(source_holder[0])

    source = _MaterializedSource(namespace=namespace, guard_name_to_value=guard_name_to_value(guard))
    source_holder.append(source)
    namespace.update(source.guard_name_to_value)
    namespace.update(resolved.name_to_binding)
    instrumented_source = analysis.instrumented_source
    linecache.cache[filename] = (len(instrumented_source), None, instrumented_source.splitlines(keepends=True), filename)
    # Sanctioned recursion channel: this is the single host-owned place where
    # validated, guard-instrumented generated source is compiled and its
    # declared exports defined.
    code = compile(instrumented_source, filename, "exec", dont_inherit=True)
    exec(code, namespace)  # noqa: S102

    for export_name in proposal.export_name_tuple:
        if export_name_to_kind[export_name] == "value":
            continue
        if not isinstance(namespace[export_name], types.FunctionType):
            raise GeneratedExecutionError(
                dispatch_node_id=node.node_id,
                outcome_event_id="",
                failure_kind="result_contract_violation",
                message=f"export {export_name!r} did not materialize as a function",
            )

    definition_list: list[GeneratedDefinition] = []
    export_name_to_value: dict[str, object] = {}
    annotations = module_annotations(namespace, resolve_deferred=True)
    export_name_to_docstring = analysis.export_name_to_docstring
    for export_name in proposal.export_name_tuple:
        value = namespace[export_name]
        definition_kind = export_name_to_kind[export_name]
        definition_id = generate_ulid()
        entry = host.ledger._append(
            DefinitionMaterializationEvent(
                definition_id=definition_id,
                export_name=export_name,
                definition_kind=definition_kind,
                source_provenance_class="generated",
                defining_source_identity=proposal.defining_source_identity,
                materialization_origin_node_id=node.node_id,
                retained_definition_id=retained_definition_id if export_name == retained_export_name else None,
            ),
            parent_event_id=dispatch_event_id,
        )
        definition = GeneratedDefinition(
            definition_id=definition_id,
            export_name=export_name,
            definition_kind=definition_kind,
            defining_source_identity=proposal.defining_source_identity,
            dispatch_node_id=node.node_id,
            materialization_event_id=entry.event_id,
        )
        definition_list.append(definition)
        if definition_kind == "value":
            annotation = annotations.get(export_name, inspect.Signature.empty)
            if annotation is not inspect.Signature.empty and annotation is not object:
                try:
                    annotation = typing.get_type_hints(
                        types.SimpleNamespace(__annotations__={export_name: annotation}),
                        globalns=namespace,
                        localns=namespace,
                        include_extras=True,
                    )[export_name]
                    value = build_value_adapter(annotation).validate_python(value)
                except Exception as error:
                    raise GeneratedExecutionError(
                        dispatch_node_id=node.node_id,
                        outcome_event_id="",
                        failure_kind="result_contract_violation",
                        message=f"value export {export_name!r} does not satisfy its annotation",
                    ) from error
            export_name_to_value[export_name] = value
            continue
        assert isinstance(value, types.FunctionType)
        if getattr(value, "__wrapped__", None) is None:
            value.__doc__ = export_name_to_docstring.get(export_name)
        reference_type = AsyncGeneratedCallableReference if inspect.iscoroutinefunction(value) else GeneratedCallableReference
        export_name_to_value[export_name] = reference_type(
            host=host,
            definition=definition,
            function=value,
            dependency_tuple=proposal.dependency_tuple,
            effect_footprint=proposal.effect_footprint,
            source=source,
        )
    # Rebind every function export to its host wrapper so that sibling calls
    # and returned siblings enter dispatch like any other invocation.
    source.bind({name: value for name, value in export_name_to_value.items() if isinstance(value, GeneratedCallableReference)})
    return tuple(definition_list), export_name_to_value


def _validate_generated_result(plan: _InvocationPlan, result: object) -> object:
    """Validate the body's result against the call contract and return the committed value."""

    try:
        return validate_result(plan.call_contract, result)
    except ValueError as error:
        outcome_event_id = plan.host.record_dispatch_outcome(plan.node, "failure", failure_class_name="GeneratedExecutionError", detail=str(error))
        raise GeneratedExecutionError(
            dispatch_node_id=plan.node.node_id,
            outcome_event_id=outcome_event_id or "",
            failure_kind="result_contract_violation",
            message=str(error),
        ) from error


def _run_generated_body(plan: _InvocationPlan) -> object:
    with (
        plan.host.execution_scope(plan.invocation_grant, plan.node, generated_source=plan.source),
        harness_span("generated_execution", {"node_id": plan.node.node_id}),
    ):
        try:
            result = plan.function(*plan.positional_arguments, **plan.keyword_arguments)
        except BaseException as exception:
            plan.host.record_dispatch_outcome(plan.node, "failure", failure_class_name=type(exception).__name__)
            raise
    return _validate_generated_result(plan, result)


async def _run_generated_body_async(plan: _InvocationPlan) -> object:
    with (
        plan.host.execution_scope(plan.invocation_grant, plan.node, generated_source=plan.source),
        harness_span("generated_execution", {"node_id": plan.node.node_id}),
    ):
        try:
            result = await typing.cast(Callable[..., typing.Awaitable[object]], plan.function)(*plan.positional_arguments, **plan.keyword_arguments)
        except BaseException as exception:
            # Cooperative cancellation is a terminal observation of this invocation.
            plan.host.record_dispatch_outcome(plan.node, "failure", failure_class_name=type(exception).__name__)
            raise
    return _validate_generated_result(plan, result)


def _dispatch_proposal(
    host: Host,
    context: ExecutionContext,
    proposal: CodeProposal,
    *,
    retained_definition_id: str | None = None,
    retained_export_name: str | None = None,
) -> DispatchResult:
    """Materialize a proposal under one dispatch node; the combined intent then invokes the entry export through its reference."""

    dispatch_kind: DispatchKind = proposal.execution_intent
    filename = f"<nighthawk-generated:{proposal.defining_source_identity[:16]}:{generate_ulid()}>"
    try:
        analysis = analyze_generated_source(proposal, filename=filename)
    except SourceRejectedError as rejection:
        raise _reject(rejection.reason, context=context, detail=rejection.detail) from None

    resolved = _resolve_dependencies(host, context, proposal.dependency_tuple, proposal.effect_footprint)
    _validate_footprint(context, proposal.effect_footprint, proposal.dependency_tuple, _effective_perimeter_authority(host, context.grant))
    invocation_grant = Grant(resolved.capability_tuple, resolved.name_to_reference)
    confinement_identity = _confirm_confinement(host, context, proposal.effect_footprint)
    node = _admit_dispatch(host, context)
    dispatch_entry = host.ledger._append(
        DispatchEvent(
            dispatch_node_id=node.node_id,
            parent_node_id=context.node.node_id,
            recursion_depth=node.recursion_depth,
            dispatch_kind=dispatch_kind,
            defining_source_identity=proposal.defining_source_identity,
            definition_id=None,
            export_name_tuple=proposal.export_name_tuple,
            dependency_name_tuple=proposal.dependency_name_tuple,
            effect_footprint=proposal.effect_footprint,
            grant_capability_name_tuple=tuple(sorted(invocation_grant.capabilities)),
            confinement_identity=confinement_identity,
        ),
        parent_event_id=host.anchor_event_id(context.node.node_id),
    )
    host.set_anchor_event_id(node.node_id, dispatch_entry.event_id)
    emit_harness_event("dispatch", {"node_id": node.node_id, "dispatch_kind": dispatch_kind, "recursion_depth": node.recursion_depth})

    with host.execution_scope(invocation_grant, node), harness_span("materialization", {"node_id": node.node_id}):
        try:
            definition_tuple, export_name_to_value = _materialize(
                host,
                node,
                proposal,
                analysis,
                resolved,
                filename=filename,
                dispatch_event_id=dispatch_entry.event_id,
                retained_definition_id=retained_definition_id,
                retained_export_name=retained_export_name,
            )
        except BaseException as exception:
            host.record_dispatch_outcome(node, "failure", failure_class_name=type(exception).__name__)
            raise
    host.record_dispatch_outcome(node, "success")

    entry_result: object = None
    entry_dispatch_node_id: str | None = None
    if proposal.execution_intent == "definition_and_invocation":
        assert proposal.entry_export_name is not None
        reference = export_name_to_value[proposal.entry_export_name]
        assert isinstance(reference, GeneratedCallableReference) and not reference.is_async
        # The same mediated invocation as any reference call: validated
        # arguments, admission, its own dispatch node, and its own outcome.
        plan = _prepare_invocation(reference, proposal.entry_argument_tuple, dict(proposal.entry_keyword_argument_name_to_value))
        entry_result = _run_generated_body(plan)
        host.record_dispatch_outcome(plan.node, "success", result=entry_result, result_type_name=value_type_name(entry_result))
        entry_dispatch_node_id = plan.node.node_id

    return DispatchResult(
        dispatch_node_id=node.node_id,
        definition_tuple=definition_tuple,
        export_name_to_value=MappingProxyType(export_name_to_value),
        entry_result=entry_result,
        entry_dispatch_node_id=entry_dispatch_node_id,
    )


def _prepare_invocation(
    reference: GeneratedCallableReference,
    positional_arguments: tuple[object, ...],
    keyword_arguments: dict[str, object],
) -> _InvocationPlan:
    """Validate, admit, and record one reference invocation before its body runs."""

    host = reference._host
    if current_host() is not host:
        raise _reject("definition_unavailable" if current_host() is not None else "no_active_run", context=None)
    host, context = _require_live_context(host)
    if reference._function.__code__ is not reference._code:
        host.record_anomaly(
            "definition_code_modified",
            node_id=context.node.node_id,
            detail=f"definition {reference.definition.definition_id} code object was replaced",
        )
    if not reference._source.guards_intact():
        host.record_anomaly(
            "namespace_guard_modified",
            node_id=context.node.node_id,
            detail=f"the execution guard of the source defining {reference.definition.definition_id} was rebound",
        )
        reference._source.bind(reference._source.guard_name_to_value)
    resolved = _resolve_dependencies(host, context, reference.dependency_tuple, reference.effect_footprint)
    _validate_footprint(context, reference.effect_footprint, reference.dependency_tuple, _effective_perimeter_authority(host, context.grant))
    carried = _argument_carried_capabilities(host, context, reference.effect_footprint, positional_arguments, keyword_arguments)
    try:
        positional, keyword = bind_and_validate_arguments(reference._call_contract, positional_arguments, keyword_arguments)
    except ValueError as error:
        raise _reject("argument_contract_violation", context=context, detail=str(error)) from error
    invocation_grant = Grant((*resolved.capability_tuple, *carried), resolved.name_to_reference)
    confinement_identity = _confirm_confinement(host, context, reference.effect_footprint)
    node = _admit_dispatch(host, context)
    dispatch_entry = host.ledger._append(
        DispatchEvent(
            dispatch_node_id=node.node_id,
            parent_node_id=context.node.node_id,
            recursion_depth=node.recursion_depth,
            dispatch_kind="invocation",
            defining_source_identity=reference.definition.defining_source_identity,
            definition_id=reference.definition.definition_id,
            argument_snapshot=snapshot_value({"positional": positional, "keyword": keyword}),
            export_name_tuple=(reference.definition.export_name,),
            dependency_name_tuple=tuple(dependency.name for dependency in reference.dependency_tuple),
            effect_footprint=reference.effect_footprint,
            grant_capability_name_tuple=tuple(sorted(invocation_grant.capabilities)),
            confinement_identity=confinement_identity,
        ),
        parent_event_id=host.anchor_event_id(context.node.node_id),
    )
    host.set_anchor_event_id(node.node_id, dispatch_entry.event_id)
    emit_harness_event("dispatch", {"node_id": node.node_id, "dispatch_kind": "invocation", "recursion_depth": node.recursion_depth})
    reference._source.bind(resolved.name_to_binding)
    return _InvocationPlan(
        host=host,
        node=node,
        invocation_grant=invocation_grant,
        function=reference._function,
        call_contract=reference._call_contract,
        source=reference._source,
        positional_arguments=positional,
        keyword_arguments=keyword,
    )


def dispatch(proposal: CodeProposal) -> DispatchResult:
    """Submit a code proposal through the sanctioned recursion channel.

    Callable from ratified interior execution inside an active agent run. The
    grant in effect is the nearest enclosing scope declaration of the caller.

    Raises:
        DispatchRejectedError: when validation, confinement, or admission fails
            before generated execution begins.
        GeneratedExecutionError: when an invoked entry export violates its
            declared call contract or a value export violates its annotation.
    """

    host, context = _require_live_context(current_host())
    return _dispatch_proposal(host, context, proposal)


def _retain_function(proposal: CodeProposal, *, export_name: str) -> RetainedFunction:
    from nighthawk.harness.generated import RetainedFunction, RetainedFunctionDefinition

    if proposal.execution_intent != "definition_only":
        raise ValueError("Function retention requires definition-only source")
    if export_name not in proposal.export_name_tuple:
        raise ValueError("The selected function must be a declared export")
    host, context = _require_live_context(current_host())
    with host._lock:
        materialized = _dispatch_proposal(host, context, proposal)
        reference = materialized.export_name_to_value[export_name]
        if not isinstance(reference, GeneratedCallableReference):
            raise TypeError("The selected export must be a callable function")
        definition = RetainedFunctionDefinition(reference.definition.definition_id, proposal, export_name, reference.is_async, host.run_id)
        function = RetainedFunction._from_materialization(definition, inspect.signature(reference))
        host._retained_function_to_reference[function] = reference
        return function


def _materialize_retained_function(function: RetainedFunction) -> GeneratedCallableReference:
    host, context = _require_live_context(current_host())
    with host._lock:
        reference = host._retained_function_to_reference.get(function)
        if reference is None:
            definition = function.definition
            materialized = _dispatch_proposal(
                host,
                context,
                definition.proposal,
                retained_definition_id=definition.definition_id,
                retained_export_name=definition.export_name,
            )
            reference = materialized.export_name_to_value[definition.export_name]
            if not isinstance(reference, GeneratedCallableReference) or reference.is_async != definition.is_async:
                raise TypeError("The retained definition must materialize as the declared callable kind")
            host._retained_function_to_reference[function] = reference
        return reference


def _execute_code(
    source: str,
    *,
    exports: Sequence[str],
    dependencies: Sequence[DependencyDeclaration] = (),
    effect_footprint: EffectFootprint = EffectFootprint.NONE,
    # Spelled out rather than aliased: Natural executions read this signature as the request type.
    intent: typing.Literal["definition_only", "definition_and_invocation"] = "definition_only",
    entry_export: str | None = None,
    entry_arguments: Sequence[object] = (),
    entry_keyword_arguments: Mapping[str, object] | None = None,
) -> DispatchResult:
    """Define Python functions from source; optionally invoke one entry export.

    Call a returned reference to invoke an already defined function.
    """

    try:
        proposal = CodeProposal(
            source=source,
            execution_intent=intent,
            export_name_tuple=tuple(exports),
            dependency_tuple=tuple(dependencies),
            effect_footprint=effect_footprint,
            entry_export_name=entry_export,
            entry_argument_tuple=tuple(entry_arguments),
            entry_keyword_argument_name_to_value=dict(entry_keyword_arguments or {}),
        )
    except ValidationError as error:
        raise _reject("proposal_invalid", context=None, detail="; ".join(str(item.get("msg", "")) for item in error.errors())) from error
    return dispatch(proposal)


def execute_code_capability(*, name: str = "execute_code") -> Capability:
    """Return the host capability that submits generated source for dispatch.

    Granting it to a Natural execution or generated execution lets that
    execution reach the sanctioned recursion channel; every request still enters
    the same dispatch mediation seam and never widens the submitting grant.
    """

    return Capability(
        name=name,
        function=_execute_code,
        authority_boundary="define and run generated Python only through host dispatch under the current grant",
    )
