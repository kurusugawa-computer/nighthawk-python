"""Public exception surface for ``nighthawk.harness``.

Every exception inherits from ``nighthawk.errors.NighthawkError`` and carries
machine-readable fields that identify the run, attribution node, and run-ledger
events involved. Messages are redacted: they name reasons and identities, never
request payloads, source text, or capability arguments.
"""

from __future__ import annotations

from typing import Literal

from nighthawk.errors import NighthawkError

__all__ = [
    "CapabilityRejectedError",
    "DispatchRejectedError",
    "DispatchRejectionReason",
    "GeneratedExecutionError",
    "GeneratedExecutionFailureKind",
    "GrantError",
    "GrantErrorReason",
    "NaturalExecutionError",
    "NaturalExecutionFailureKind",
    "PerimeterEffectRejectedError",
    "PerimeterEffectRejectionReason",
    "RunFailedError",
    "RunRejectedError",
    "RunRejectionReason",
    "RunTerminalKind",
    "RunTerminationError",
    "TerminationError",
    "TerminationFailureReason",
]


type NaturalExecutionFailureKind = Literal[
    "executor_error",
    "write_set_invalid",
    "disallowed_outcome_kind",
    "raise_outcome",
    "grant_violation",
    "mediation_bypass",
    "allocation_exhausted",
    "commit_failed",
    "core_failure",
    "interrupted",
    "terminated",
]


class NaturalExecutionError(NighthawkError):
    """One Natural execution ended in a failed outcome.

    Raised by the host inside the containing Natural-function invocation after the
    failed outcome event has been appended to the run ledger. No declared write
    binding of the failed execution becomes visible.

    ``failure_kind`` distinguishes executor errors, invalid write sets, outcome
    kinds the block or the host does not accept, ``raise`` outcomes requested by
    the Natural contract (``raise_error_type_name`` then names the requested
    error type), grant violations (implicit references or tools visible outside
    the grant), mediation bypasses (a core oversight hook installed around the
    execution, or an operation evaluated inside a host gateway allowance),
    allocation exhaustion, and host termination.

    Inside an agent run a ``raise`` outcome does not surface as the requested
    exception type: ``except ValueError`` in the containing invocation will not
    catch it. Catch ``NaturalExecutionError`` and branch on
    ``raise_error_type_name`` instead. Inside an agent run the core ``return``
    outcome is never offered to the executor and is reported as
    ``disallowed_outcome_kind`` if an executor emits it anyway: a model-selected
    return would skip the containing function's deterministic Python and is not
    a declared write binding. Outside an agent run the core library's ordinary
    semantics apply.
    """

    natural_execution_id: str
    outcome_event_id: str
    attribution_node_id: str
    failure_kind: NaturalExecutionFailureKind
    raise_error_type_name: str | None

    def __init__(
        self,
        *,
        natural_execution_id: str,
        outcome_event_id: str,
        attribution_node_id: str,
        failure_kind: NaturalExecutionFailureKind,
        message: str,
        raise_error_type_name: str | None = None,
    ) -> None:
        self.natural_execution_id = natural_execution_id
        self.outcome_event_id = outcome_event_id
        self.attribution_node_id = attribution_node_id
        self.failure_kind = failure_kind
        self.raise_error_type_name = raise_error_type_name
        super().__init__(f"Natural execution {natural_execution_id} failed ({failure_kind}): {message}")


type DispatchRejectionReason = Literal[
    "no_active_run",
    "run_ended",
    "run_terminated",
    "node_terminated",
    "node_ended",
    "proposal_invalid",
    "intent_mismatch",
    "source_syntax_error",
    "raw_dynamic_execution",
    "introspection_not_allowed",
    "reserved_name",
    "unsupported_syntax",
    "materialization_effect",
    "decorator_not_allowed",
    "export_undefined",
    "export_kind_unsupported",
    "undeclared_definition",
    "undeclared_dependency",
    "undeclared_namespace_write",
    "dependency_shadowed",
    "dependency_missing",
    "dependency_kind_mismatch",
    "dependency_type_invalid",
    "dependency_type_mismatch",
    "capability_not_granted",
    "footprint_capability_undeclared",
    "footprint_mutable_reference_undeclared",
    "footprint_perimeter_undeclared",
    "perimeter_outside_authority",
    "argument_capability_undeclared",
    "argument_capability_unauthorized",
    "argument_contract_violation",
    "confinement_unavailable",
    "allocation_exhausted",
    "definition_unavailable",
    "unmediated_execution",
]


class DispatchRejectedError(NighthawkError):
    """The host rejected a dispatch request before generated execution began.

    ``rejection_event_id`` identifies the run-ledger event recorded for the
    rejection when one exists (allocation exhaustion always records one).
    ``unmediated_execution`` is raised by the guard compiled into every
    generated function body when generated code starts or resumes outside a
    live generated execution of its own dispatch: a leaked helper, closure,
    method, generator, or coroutine cannot run after its dispatch returned or
    after the run ended.
    """

    reason: DispatchRejectionReason
    detail: str | None
    attribution_node_id: str | None
    rejection_event_id: str | None

    def __init__(
        self,
        *,
        reason: DispatchRejectionReason,
        detail: str | None = None,
        attribution_node_id: str | None = None,
        rejection_event_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.detail = detail
        self.attribution_node_id = attribution_node_id
        self.rejection_event_id = rejection_event_id
        detail_part = f": {detail}" if detail else ""
        super().__init__(f"dispatch rejected ({reason}){detail_part}")


type GeneratedExecutionFailureKind = Literal["result_contract_violation"]


class GeneratedExecutionError(NighthawkError):
    """A generated execution completed but violated its call contract.

    ``result_contract_violation`` reports a result outside the declared return
    type or a value export outside its annotation.
    """

    dispatch_node_id: str
    outcome_event_id: str
    failure_kind: GeneratedExecutionFailureKind

    def __init__(
        self,
        *,
        dispatch_node_id: str,
        outcome_event_id: str,
        failure_kind: GeneratedExecutionFailureKind,
        message: str,
    ) -> None:
        self.dispatch_node_id = dispatch_node_id
        self.outcome_event_id = outcome_event_id
        self.failure_kind = failure_kind
        super().__init__(f"generated execution {dispatch_node_id} failed ({failure_kind}): {message}")


type GrantErrorReason = Literal["ambiguous_name", "authority_widening", "invalid_entry", "no_active_run"]


class GrantError(NighthawkError):
    """A grant could not be composed or declared.

    ``ambiguous_name`` reports a name bound to two different objects, and
    ``authority_widening`` reports capabilities a nested scope tried to add
    beyond the authority of the submitting execution.
    """

    reason: GrantErrorReason
    name_tuple: tuple[str, ...]

    def __init__(self, *, reason: GrantErrorReason, name_tuple: tuple[str, ...] = (), detail: str | None = None) -> None:
        self.reason = reason
        self.name_tuple = name_tuple
        name_part = f" {', '.join(name_tuple)}" if name_tuple else ""
        detail_part = f": {detail}" if detail else ""
        super().__init__(f"grant error ({reason}){name_part}{detail_part}")


type RunRejectionReason = Literal[
    "perimeter_policy_widening",
    "capability_outside_perimeter_policy",
    "input_type_invalid",
    "invalid_declaration",
    "executor_steps_unobservable",
]


class RunRejectedError(NighthawkError):
    """A run-entry declaration could not be admitted, so the run did not start.

    ``executor_steps_unobservable`` reports a step executor whose LLM requests
    the host cannot present for admission one by one while the platform has not
    accepted aggregate executor accounting.
    """

    reason: RunRejectionReason
    detail: str | None

    def __init__(self, *, reason: RunRejectionReason, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        detail_part = f": {detail}" if detail else ""
        super().__init__(f"run rejected ({reason}){detail_part}")


type RunTerminalKind = Literal["validation_rejection", "uncaught_exception", "host_termination"]


class RunFailedError(NighthawkError):
    """An agent run ended without an accepted typed result.

    ``terminal_event_id`` identifies the run-end event in the run ledger.
    """

    run_id: str
    terminal_event_id: str
    terminal_kind: RunTerminalKind

    def __init__(self, *, run_id: str, terminal_event_id: str, terminal_kind: RunTerminalKind, message: str) -> None:
        self.run_id = run_id
        self.terminal_event_id = terminal_event_id
        self.terminal_kind = terminal_kind
        super().__init__(f"run {run_id} failed ({terminal_kind}): {message}")


class RunTerminationError(NighthawkError):
    """The run could not reach its run-end seam because interior branches did not cease.

    Raised when the platform termination mechanism does not establish cessation
    of nonterminal dispatch branches at run end. No run-end event exists; the
    affected dispatch nodes remain authoritatively nonterminal in the run ledger,
    and ``anomaly_event_id`` identifies the recorded trace anomaly. The host
    refuses further interior operations of the run regardless.
    """

    run_id: str
    anomaly_event_id: str
    nonterminal_node_id_tuple: tuple[str, ...]

    def __init__(self, *, run_id: str, anomaly_event_id: str, nonterminal_node_id_tuple: tuple[str, ...]) -> None:
        self.run_id = run_id
        self.anomaly_event_id = anomaly_event_id
        self.nonterminal_node_id_tuple = nonterminal_node_id_tuple
        super().__init__(f"run {run_id} could not establish cessation of {len(nonterminal_node_id_tuple)} interior branch(es)")


type PerimeterEffectRejectionReason = Literal["attempt_record_failed", "outside_effective_authority", "unmediated_python_effect"]


class PerimeterEffectRejectedError(NighthawkError):
    """A perimeter effect was not attempted.

    The durable attempt record could not be written, the effect lies outside the effective perimeter authority,
    or a Python audit event exposed an unmediated effect.
    For an audit rejection, ``capability_name`` identifies the observed event.
    """

    capability_name: str
    reason: PerimeterEffectRejectionReason
    attribution_node_id: str

    def __init__(self, *, capability_name: str, reason: PerimeterEffectRejectionReason, attribution_node_id: str) -> None:
        self.capability_name = capability_name
        self.reason = reason
        self.attribution_node_id = attribution_node_id
        super().__init__(f"perimeter effect of {capability_name!r} rejected ({reason})")


type TerminationFailureReason = Literal["unknown_node", "not_descendant", "cessation_not_established"]


class TerminationError(NighthawkError):
    """A descendant termination request did not establish termination."""

    target_dispatch_node_id: str
    reason: TerminationFailureReason

    def __init__(self, *, target_dispatch_node_id: str, reason: TerminationFailureReason, detail: str | None = None) -> None:
        self.target_dispatch_node_id = target_dispatch_node_id
        self.reason = reason
        detail_part = f": {detail}" if detail else ""
        super().__init__(f"termination of {target_dispatch_node_id} failed ({reason}){detail_part}")


class CapabilityRejectedError(NighthawkError):
    """The host or a capability's own authority boundary rejected a request.

    ``reason_code`` is a stable machine-branchable code. Host codes are
    ``no_active_run``, ``run_ended``, ``run_terminated``, ``not_in_current_grant``,
    ``node_terminated``, ``node_ended``, ``request_type_invalid``, and
    ``result_type_invalid``;
    capability families document their own policy codes. The message never
    carries request payload content.
    """

    capability_name: str
    reason_code: str
    detail: str | None

    def __init__(self, *, capability_name: str, reason_code: str, detail: str | None = None) -> None:
        self.capability_name = capability_name
        self.reason_code = reason_code
        self.detail = detail
        detail_part = f" ({detail})" if detail else ""
        super().__init__(f"{capability_name}: {reason_code}{detail_part}")
