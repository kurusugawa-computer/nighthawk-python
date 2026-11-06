"""Canonical typed operations shared by explicit operator Python and terminal dispatch."""

from __future__ import annotations

import keyword
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from ._history import page_limits
from ._inspection import _ProjectionBudget
from ._operator_control import _OperatorControl, is_operator_control
from ._operator_records import (
    BindingMutationResult,
    DeleteLocalRequest,
    ExecutionObservation,
    ExitRequest,
    HelpRequest,
    InspectLocalRequest,
    InterruptBackgroundRequest,
    InterruptExecutionRequest,
    InterruptionResult,
    ListBackgroundRequest,
    ListOperationsRequest,
    LocalBindingObservation,
    LocalPage,
    LocalQuery,
    OperationDescriptor,
    OperationParameter,
    OperatorRequest,
    OperatorResult,
    ReadEvidenceRequest,
    ReadExecutionRequest,
    ReadHistoryRequest,
    ReadLocalsRequest,
    ReadTextRequest,
    RegisterBackgroundRequest,
    RenameLocalRequest,
    SearchHistoryRequest,
)
from ._source import require_operator

if TYPE_CHECKING:
    from ._session import Session


_DEFAULT_LOCAL_QUERY = LocalQuery()


def filter_local_page(snapshot: LocalPage, query: LocalQuery) -> LocalPage:
    """Filter an immutable observation without touching live Python values."""
    if query.revision is not None and query.revision != snapshot.revision:
        snapshot = replace(snapshot, unavailable_reason="Requested revision is unavailable; returning the retained observation")
    count, remaining = page_limits(query.starting_index, query.maximum_item_count, query.maximum_character_count)
    selected = tuple(
        binding
        for binding in snapshot.binding_tuple
        if query.name in binding.name
        and query.type_description in binding.type_description
        and (query.exposure is None or query.exposure == binding.exposure)
    )
    bindings: list[LocalBindingObservation] = []
    continuation = None
    for i in range(query.starting_index, len(selected)):
        if len(bindings) == count or remaining == 0:
            continuation = i
            break
        binding = selected[i]
        preview = binding.preview[:remaining]
        remaining -= len(preview)
        bindings.append(replace(binding, preview=preview, incomplete=binding.incomplete or len(preview) < len(binding.preview)))
    return replace(
        snapshot,
        binding_tuple=tuple(bindings),
        next_starting_index=continuation,
        incomplete=snapshot.incomplete or continuation is not None or any(item.incomplete for item in bindings),
    )


class SessionLocals(_OperatorControl):
    """Bounded owner-thread snapshots. Candidate exposure never claims a live grant."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._snapshot = LocalPage((), time.time(), 0)

    def _capture(self, observed_at: float, revision: int) -> None:
        bindings: list[LocalBindingObservation] = []
        session = self._session
        for name, value in tuple(session.binding_name_to_value.items()):
            if type(name) is not str or name.startswith("__"):
                continue
            if len(bindings) == 1000:
                break
            budget = _ProjectionBudget(256, entries=40)
            projected = budget.value(value)
            preview = repr(projected)[:256] if not (projected is None and value is not None) else "<preview unavailable>"
            if is_operator_control(value) or value is session or session.background._is_registered(value):
                exposure, description = "controller", "Operator control; never an Agent capability"
            elif not session._eligible(name, value):
                exposure, description = "unavailable", "Not eligible for Agent admission"
            elif name.startswith("_") and name not in session._exposed_name_set:
                exposure, description = "private", "Private binding; not a future run candidate"
            else:
                exposure, description = "candidate", "Candidate for a future run; actual admission depends on that run's grant"
            bindings.append(
                LocalBindingObservation(
                    name,
                    type.__dict__["__name__"].__get__(type(value)),
                    preview,
                    exposure,
                    description,
                    observed_at,
                    revision,
                    budget.incomplete or len(repr(projected)) > 256,
                )
            )
        self._snapshot = LocalPage(
            tuple(bindings),
            observed_at,
            revision,
            incomplete=len(bindings) == 1000,
            session_name_shadowed=session.binding_name_to_value.get("session") is not session,
        )

    def read(self, query: LocalQuery = _DEFAULT_LOCAL_QUERY) -> LocalPage:
        require_operator("local_observation")
        session = self._session
        session._require_open()
        if session._can_observe_live() and query.revision is None:
            session._observe()
        return filter_local_page(self._snapshot, query)

    def inspect(self, name: str) -> LocalBindingObservation:
        require_operator("local_inspection")
        page = self.read(LocalQuery(name=name))
        item = next((binding for binding in self._snapshot.binding_tuple if binding.name == name), None)
        if item is not None:
            return item
        return LocalBindingObservation(
            name, "unknown", "<not observed>", "unavailable", "No retained observation", page.observed_at, page.revision, True, False
        )

    def _mutate(self, name: str, new_name: str | None) -> BindingMutationResult:
        require_operator("local_mutation")
        session = self._session
        with session._namespace_lock:
            session._require_open()
            revision = session._observation_revision
            if session._active_execution is not None and not session._at_operator_boundary():
                return BindingMutationResult("busy", name, new_name, revision, "The namespace is busy; no operation was queued")
            if session._closing:
                return BindingMutationResult("unavailable", name, new_name, revision, "Session close is pending")
            for target in (name, new_name):
                if target is None:
                    continue
                if target in ("session", "nh") or target.startswith("__") or not target.isidentifier() or keyword.iskeyword(target):
                    return BindingMutationResult(
                        "unavailable", name, new_name, revision, "Controller/interpreter names are unavailable to binding organization"
                    )
            if name not in session.binding_name_to_value:
                return BindingMutationResult("missing", name, new_name, revision)
            if new_name is not None and new_name in session.binding_name_to_value:
                return BindingMutationResult("collision", name, new_name, revision)
            value = session.binding_name_to_value.pop(name)
            explicitly_exposed = name in session._exposed_name_set
            session._exposed_name_set.discard(name)
            if new_name is not None:
                session.binding_name_to_value[new_name] = value
                if explicitly_exposed:
                    session._exposed_name_set.add(new_name)
            session._observe()
            return BindingMutationResult("applied", name, new_name, session._observation_revision)

    def delete(self, name: str) -> BindingMutationResult:
        return self._mutate(name, None)

    def rename(self, name: str, new_name: str) -> BindingMutationResult:
        return self._mutate(name, new_name)


class SessionExecution(_OperatorControl):
    def __init__(self, session: Session) -> None:
        self._session = session
        self._snapshot = ExecutionObservation(None, (), "idle", "Idle", None, time.time(), 0, False)

    def _capture(self, observed_at: float, revision: int) -> None:
        session = self._session
        execution = session._active_execution or session._last_execution
        state = "closed" if session._disposed else execution.status if execution is not None else "idle"
        activity = "Idle"
        if execution is not None and execution.outcome is None:
            activity = "Running Python" if execution._mode == "python" else "Running Natural"
        self._snapshot = ExecutionObservation(
            execution.cell_id if execution is not None else None,
            execution.run_id_tuple if execution is not None else (),
            state,
            activity,
            execution._started_at if execution is not None else None,
            observed_at,
            revision,
            execution is not None and execution.outcome is None,
            "Provider activity is not observed" if execution is not None and execution._mode == "natural_language" else None,
        )

    def read(self) -> ExecutionObservation:
        require_operator("execution_observation")
        if self._session._can_observe_live():
            self._session._observe()
        return self._snapshot

    def interrupt(self) -> InterruptionResult:
        require_operator("execution_interruption")
        execution = self._session._active_execution
        if execution is None:
            return InterruptionResult("already_finished", self._snapshot.cell_id)
        execution.request_interrupt()
        return InterruptionResult("requested", execution.cell_id, "A cancellation request does not establish cessation")


_PARAMETER_NAME = OperationParameter("name", "str", True)
_DESCRIPTORS = (
    OperationDescriptor(
        "session.locals.read",
        "(query: LocalQuery = LocalQuery()) -> LocalPage",
        "Filter retained local binding observations",
        (OperationParameter("query", "LocalQuery"),),
        "query",
    ),
    OperationDescriptor("session.locals.inspect", "(name: str) -> LocalBindingObservation", "Inspect one local binding", (_PARAMETER_NAME,), "query"),
    OperationDescriptor(
        "session.locals.delete", "(name: str) -> BindingMutationResult", "Delete a local working binding", (_PARAMETER_NAME,), "mutation"
    ),
    OperationDescriptor(
        "session.locals.rename",
        "(name: str, new_name: str) -> BindingMutationResult",
        "Rename without overwriting another binding",
        (_PARAMETER_NAME, OperationParameter("new_name", "str", True)),
        "mutation",
    ),
    OperationDescriptor("session.execution.read", "() -> ExecutionObservation", "Read the last observed execution state", (), "query"),
    OperationDescriptor("session.execution.interrupt", "() -> InterruptionResult", "Request foreground interruption", (), "lifecycle"),
    OperationDescriptor(
        "session.background.list", "() -> tuple[BackgroundObservation, ...]", "Inspect explicitly owned background work", (), "query"
    ),
    OperationDescriptor(
        "session.background.register",
        "(work: ManagedBackgroundWork) -> str",
        "Register a trusted owner adapter already retained in a Python binding; registration starts no work",
        (OperationParameter("work", "ManagedBackgroundWork", True),),
        "mutation",
    ),
    OperationDescriptor(
        "session.background.interrupt",
        "(work_id: str) -> InterruptionResult",
        "Ask one owner to interrupt its work",
        (OperationParameter("work_id", "str", True),),
        "lifecycle",
    ),
    OperationDescriptor(
        "session.history.read",
        "(*, starting_index: int = 0, horizon: int | None = None, maximum_item_count: int = 20, maximum_character_count: int = 6000, filters: HistoryFilter = HistoryFilter()) -> ConversationPage",
        "Read or filter conversation observations",
        (
            OperationParameter("starting_index", "int", default="0"),
            OperationParameter("horizon", "int | None", default="None"),
            OperationParameter("maximum_item_count", "int", default="20"),
            OperationParameter("maximum_character_count", "int", default="6000"),
            OperationParameter("filters", "HistoryFilter"),
        ),
        "query",
    ),
    OperationDescriptor(
        "session.history.search",
        "(query: str, *, starting_index: int = 0, horizon: int | None = None, maximum_item_count: int = 20, maximum_character_count: int = 6000, filters: HistoryFilter = HistoryFilter()) -> ConversationPage",
        "Search literal text in retained conversation observations",
        (
            OperationParameter("query", "str", True),
            OperationParameter("starting_index", "int", default="0"),
            OperationParameter("horizon", "int | None", default="None"),
            OperationParameter("maximum_item_count", "int", default="20"),
            OperationParameter("maximum_character_count", "int", default="6000"),
            OperationParameter("filters", "HistoryFilter"),
        ),
        "query",
    ),
    OperationDescriptor(
        "session.history.text",
        "(record_index: int) -> PlainTextObservation",
        "Read semantic text and original-source availability",
        (OperationParameter("record_index", "int", True),),
        "query",
    ),
    OperationDescriptor(
        "session.read_run_evidence",
        "(run_id: str, request: HistoryQuery = HistoryQuery(), *, bounds: HistoryBounds = HistoryBounds()) -> HistoryProjection",
        "Read retained authoritative run evidence",
        (OperationParameter("run_id", "str", True), OperationParameter("request", "HistoryQuery"), OperationParameter("bounds", "HistoryBounds")),
        "query",
    ),
    OperationDescriptor(
        "session.operations.list",
        "(query: str = '') -> tuple[OperationDescriptor, ...]",
        "Search canonical operation metadata",
        (OperationParameter("query", "str", default="''"),),
        "query",
    ),
    OperationDescriptor(
        "session.help",
        "(operation_name: str | None = None) -> tuple[OperationDescriptor, ...]",
        "Describe canonical Python operations",
        (OperationParameter("operation_name", "str | None", default="None"),),
        "query",
    ),
    OperationDescriptor("session.exit", "() -> ExitResult", "Refuse exit if owned background work has not ceased", (), "lifecycle"),
)


class SessionOperations(_OperatorControl):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, query: str = "") -> tuple[OperationDescriptor, ...]:
        require_operator("operation_discovery")
        session = self._session
        busy = session._active_execution is not None and not session._at_operator_boundary()
        descriptors = []
        for descriptor in _DESCRIPTORS:
            if query.casefold() not in (descriptor.name + " " + descriptor.description).casefold():
                continue
            reason = ""
            if descriptor.kind == "mutation" and busy:
                reason = "The namespace is busy; mutations are not queued"
            elif descriptor.kind == "mutation" and session._closing:
                reason = "Session close is pending"
            elif session._disposed and descriptor.name not in ("session.help", "session.operations.list", "session.execution.read", "session.exit"):
                reason = "Session is closed"
            descriptors.append(replace(descriptor, available=not reason, unavailable_reason=reason))
        return tuple(descriptors)

    def invoke(self, request: OperatorRequest) -> OperatorResult:
        require_operator("operator_dispatch")
        session = self._session
        match request:
            case ReadLocalsRequest():
                return session.locals.read(request.query)
            case InspectLocalRequest():
                return session.locals.inspect(request.name)
            case DeleteLocalRequest():
                return session.locals.delete(request.name)
            case RenameLocalRequest():
                return session.locals.rename(request.name, request.new_name)
            case ReadExecutionRequest():
                return session.execution.read()
            case InterruptExecutionRequest():
                return session.execution.interrupt()
            case ListBackgroundRequest():
                return session.background.list()
            case RegisterBackgroundRequest():
                if session._active_execution is not None and not session._at_operator_boundary():
                    raise ValueError("The namespace is busy; registration was not queued")
                return session.background.register(session.binding_name_to_value[request.binding_name])
            case InterruptBackgroundRequest():
                return session.background.interrupt(request.work_id)
            case ExitRequest():
                return session.exit()
            case HelpRequest():
                return session.help(request.operation_name)
            case ListOperationsRequest():
                return self.list(request.query)
            case ReadHistoryRequest():
                return session.history.read(
                    starting_index=request.starting_index,
                    horizon=request.horizon,
                    maximum_item_count=request.maximum_item_count,
                    maximum_character_count=request.maximum_character_count,
                    filters=request.filters,
                )
            case SearchHistoryRequest():
                return session.history.search(
                    request.query,
                    starting_index=request.starting_index,
                    horizon=request.horizon,
                    maximum_item_count=request.maximum_item_count,
                    maximum_character_count=request.maximum_character_count,
                    filters=request.filters,
                )
            case ReadEvidenceRequest():
                return session.read_run_evidence(request.run_id, request.query, bounds=request.bounds)
            case ReadTextRequest():
                return session.history.text(request.record_index)
        raise TypeError("Unsupported typed operator request")
