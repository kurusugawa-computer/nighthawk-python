"""Foreground Session ownership, cell drivers, and whole-Natural-call mediation."""

from __future__ import annotations

import asyncio
import builtins
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from types import MethodType, ModuleType
from typing import Any

import nighthawk as nh
from nighthawk._identifiers import generate_ulid
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.harness import model_runner
from nighthawk.core.calls import FunctionCall, function_call_scope
from nighthawk.harness._host import Host
from nighthawk.harness.context import current_host
from nighthawk.harness.dispatch import AsyncGeneratedCallableReference, GeneratedCallableReference
from nighthawk.harness.errors import CapabilityRejectedError, RunRejectedError, RunTerminationError
from nighthawk.harness.evidence import EvidenceReference, resolve_evidence
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import RunEndEvent, RunLedger
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.run_contract import ApprovedFunctionDefinition, AsyncScaffoldComposition, RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.runner import Runner
from nighthawk.parts.conversation import ContextQuery, ConversationInput, ConversationResult
from nighthawk.parts.evidence import EvidenceBounds, EvidenceProjection, EvidenceQuery, project_evidence
from nighthawk.session._background import SessionBackground
from nighthawk.session._bindings import _binding_scope, _SessionBindings
from nighthawk.session._conversation import ConversationCompositionBuilder, build_conversation_composition
from nighthawk.session._functions import SessionFunction, _declare_function, _is_session_function
from nighthawk.session._history import SessionHistory, page_limits
from nighthawk.session._interpreter import _Interpreter
from nighthawk.session._queries import _QuerySelection
from nighthawk.session._records import CellInput, CellMode, CellOutcome, CellOutput, CellOutputPage, CellStatus
from nighthawk.session._source import IncludedSourceApproval, RatifiedFunctionSource, _SourceRegistry, require_operator
from nighthawk.session.controls import OperatorControl, is_operator_control
from nighthawk.session.errors import SessionBusyError, SessionClosedError, SessionLookupError, SessionWaitContextError
from nighthawk.session.formatting import format_error, format_result
from nighthawk.session.operator import SessionExecution, SessionLocals, SessionOperations
from nighthawk.session.operator_contract import BackgroundObservation, ExitResult, OperationDescriptor

_DEFAULT_HISTORY_QUERY = EvidenceQuery()
_DEFAULT_HISTORY_BOUNDS = EvidenceBounds()


class _CellRunHandle(RunHandle):
    def __init__(self, execution: CellExecution) -> None:
        super().__init__()
        self.execution = execution

    def _attach(self, host: Host) -> None:
        super()._attach(host)
        self.execution._admitted(self)


class CellExecution:
    """One queued cell driven by exactly one wait operation at a time.

    Completed waits return the same immutable outcome without notification
    replay. An unresolved run keeps admission blocked and retains cleanup;
    reattaching a later waiter never executes the cell body again.
    """

    def __init__(self, session: Session, cell: CellInput) -> None:
        self.cell_id = generate_ulid()
        self._session: Session | None = session
        self._cell: CellInput | None = cell
        self._status: CellStatus = "queued"
        self._outcome: CellOutcome | None = None
        self._output_list: list[CellOutput] = []
        self._run_id_list: list[str] = []
        self._handle_list: list[_CellRunHandle] = []
        self._driver_task: asyncio.Task[Any] | None = None
        self._driver_loop: asyncio.AbstractEventLoop | None = None
        self._driver_lock = threading.Lock()
        self._callback: Callable[[CellOutput], None] | None = None
        self._interrupted = False
        self._body_unwound = False
        self._driver_thread_id: int | None = None
        self._mode = session.classify_input(cell)
        self._started_at: float | None = None

    @property
    def status(self) -> CellStatus:
        return self._status

    @property
    def outcome(self) -> CellOutcome | None:
        return self._outcome

    @property
    def run_id_tuple(self) -> tuple[str, ...]:
        return tuple(self._run_id_list)

    def _admitted(self, handle: _CellRunHandle) -> None:
        assert self._session is not None and handle.run_id is not None
        self._handle_list.append(handle)
        self._run_id_list.append(handle.run_id)
        self._session._run_id_to_ledger[handle.run_id] = handle.ledger
        # Record identity before body execution; presentation cannot interrupt admission.
        self._record("execution", "run admitted", run_id=handle.run_id, event_id=handle.ledger.entries[0].event_id, notify=False)

    def _record(
        self,
        kind: str,
        text: str,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        notify: bool = True,
        semantic: bool = True,
        evidence_reference_tuple: tuple[EvidenceReference, ...] = (),
    ) -> None:
        output = CellOutput(self.cell_id, kind, text[:6000], run_id, event_id, len(text) > 6000, evidence_reference_tuple)
        self._output_list.append(output)
        if semantic and self._session is not None:
            self._session.history._append(cell_id=self.cell_id, kind=kind, text=text, run_id=run_id, event_id=event_id)
        callback = self._callback
        if self._session is not None:
            self._session._observe()
        if notify and callback is not None:
            try:
                callback(output)
            except Exception as error:  # noqa: BLE001 - Record observation or delivery failure without changing execution outcomes.
                self._callback = None
                self._record("delivery_diagnostic", f"output callback failed: {type(error).__name__}", notify=False, semantic=False)

    def _result(self, value: object) -> None:
        if value is not None:
            self._record("result", format_result(value))

    def read_output(
        self,
        *,
        starting_index: int = 0,
        maximum_item_count: int = 20,
        maximum_character_count: int = 6000,
    ) -> CellOutputPage:
        count, remaining = page_limits(starting_index, maximum_item_count, maximum_character_count)
        outputs: list[CellOutput] = []
        continuation: int | None = None
        for i in range(starting_index, len(self._output_list)):
            if len(outputs) == count or remaining == 0:
                continuation = i
                break
            output = self._output_list[i]
            text = output.text[:remaining]
            outputs.append(replace(output, text=text, incomplete=output.incomplete or len(text) < len(output.text)))
            remaining -= len(text)
        return CellOutputPage(tuple(outputs), continuation, continuation is not None or any(output.incomplete for output in outputs))

    def request_interrupt(self) -> None:
        """Request cooperative cancellation; completion requires actual unwind."""

        require_operator("cell_interrupt")
        if self._outcome is not None:
            return
        self._interrupted = True
        if self._status == "queued":
            self._finish("cancelled")
            return
        self._status = "interruption_requested"
        for handle in self._handle_list:
            if not handle.ledger.select(RunEndEvent):
                handle.terminate("cell interruption")
        if self._driver_task is not None and self._driver_loop is not None:
            task = self._driver_task

            def cancel_current_driver() -> None:
                if self._outcome is None and self._driver_task is task:
                    task.cancel()

            self._driver_loop.call_soon_threadsafe(cancel_current_driver)

    def wait(self, *, on_output: Callable[[CellOutput], None] | None = None) -> CellOutcome:
        """Drive unfinished work only on a thread with no running event loop."""

        if self._outcome is not None:
            return self._outcome
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise SessionWaitContextError("use wait_async while this thread has a running event loop")
        return asyncio.run(self.wait_async(on_output=on_output))

    async def wait_async(self, *, on_output: Callable[[CellOutput], None] | None = None) -> CellOutcome:
        """Run on the caller's loop, or attach exclusively to pending cleanup."""

        require_operator("cell_wait")
        if self._outcome is not None:
            return self._outcome
        if not self._driver_lock.acquire(blocking=False):
            raise SessionBusyError("this cell already has a wait driver")
        self._driver_task = asyncio.current_task()
        self._driver_thread_id = threading.get_ident()
        self._driver_loop = asyncio.get_running_loop()
        self._callback = on_output
        owner_session = self._session
        try:
            if self._body_unwound:
                self._finish_pending()
                return self._finish("cancelled" if self._interrupted else "failure")
            assert self._session is not None and self._cell is not None
            self._status = "running"
            self._started_at = time.time()
            self._session._observe()
            try:
                # Publish the running boundary to the caller's event loop before
                # entering synchronous Python. Cancellation here still has one outcome.
                await asyncio.sleep(0)
                with function_call_scope(self._session):
                    if self._mode == "python":
                        text = self._cell.text
                        if self._cell.mode is None:
                            text = text[2:]
                        await self._session._interpreter.execute(text, self.cell_id, self._result)
                    else:
                        await self._session._conversation(self, self._cell.text)
            except RunTerminationError:
                self._body_unwound = True
                self._status = "interruption_requested" if self._interrupted else "running"
                raise
            except BaseException as error:
                self._body_unwound = True
                interrupted = not isinstance(error, Exception)
                propagate = interrupted and not (isinstance(error, asyncio.CancelledError) and self._interrupted)
                if interrupted:
                    self._interrupted = True
                self._record("error", format_error(error), notify=False)
                self._finish_pending()
                outcome = self._finish("cancelled" if self._interrupted else "failure")
                if propagate:
                    raise
                return outcome
            self._body_unwound = True
            # Operator Python may catch a run's unresolved-termination error.
            # Its return cannot release the cleanup owner or establish cessation.
            self._finish_pending()
            return self._finish("cancelled" if self._interrupted else "success")
        finally:
            self._callback = None
            self._driver_task = None
            self._driver_thread_id = None
            self._driver_loop = None
            self._driver_lock.release()
            if owner_session is not None and owner_session._exit_requested and self._outcome is not None:
                owner_session.close()

    def _finish_pending(self) -> None:
        for handle in self._handle_list:
            if not handle.ledger.select(RunEndEvent):
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None
                if any(loop is not current_loop for loop in handle.pending_branch_loop_tuple()):
                    raise SessionWaitContextError("pending async branches require their original owning loop")
                handle.finish_pending()

    def _finish(self, status: CellStatus) -> CellOutcome:
        self._status = status
        self._record("status", status, notify=False)
        outcome = CellOutcome(status, self.cell_id, tuple(self._output_list), self.run_id_tuple)
        self._outcome = outcome
        session = self._session
        if session is not None:
            with session._namespace_lock:
                session.history._cell_id_to_outcome[self.cell_id] = status
                session.history._cell_id_to_outcome_index[self.cell_id] = session.history._count - 1
                session._active_execution = None
                session._last_execution = self
                session._observe()
        self._handle_list.clear()
        self._cell = None
        self._session = None
        return outcome


class SessionManager:
    """Reusable construction configuration without a Session registry."""

    def __init__(
        self,
        *,
        runner: Runner,
        run_entry_grant: Grant = Grant.EMPTY,
        composition_perimeter_policy: PerimeterPolicy = PerimeterPolicy.NONE,
        conversation_composition_builder: ConversationCompositionBuilder = build_conversation_composition,
        ratified_function_source_tuple: tuple[RatifiedFunctionSource, ...] = (),
        included_source_approval_tuple: tuple[IncludedSourceApproval, ...] = (),
        function_to_result_type: Mapping[Callable[..., object], object] | None = None,
    ) -> None:
        require_operator("session_configuration")
        self._runner = runner
        self._grant = run_entry_grant
        self._policy = composition_perimeter_policy
        self._builder = conversation_composition_builder
        self._registrations = tuple(ratified_function_source_tuple)
        self._includes = tuple(included_source_approval_tuple)
        self._function_to_result_type = dict(function_to_result_type or {})

    def create_session(self) -> Session:
        require_operator("session_creation")
        return Session(self)


class Session(OperatorControl):
    """One persistent Python namespace and foreground cell lifecycle.

    The application owns its loop, streams, signals, working directory, and
    import environment. Only uniquely owned source/module entries are removed
    on confirmed close. Arbitrary Python task/thread owners must join their work.
    """

    def __init__(self, manager: SessionManager) -> None:
        require_operator("session_creation")
        self.session_id = generate_ulid()
        self.history = SessionHistory()
        self._runner = manager._runner
        self._grant = manager._grant
        self._policy = manager._policy
        self._builder = manager._builder
        self._sources = _SourceRegistry(manager._registrations, manager._includes, manager._function_to_result_type)
        self._module_name = f"_nighthawk_session_{self.session_id}"
        self._module: ModuleType | None = ModuleType(self._module_name)
        self.binding_name_to_value = self._module.__dict__
        self.binding_name_to_value.update(session=self, nh=nh, __builtins__=builtins)
        self._interpreter = _Interpreter(self.session_id, self.binding_name_to_value, self._sources)
        self._exposed_name_set: set[str] = set()
        self._run_id_to_ledger: dict[str, RunLedger] = {}
        self._active_execution: CellExecution | None = None
        self._closing = False
        self._disposed = False
        self._exit_requested = False
        self._last_execution: CellExecution | None = None
        self._observation_revision = 0
        self._namespace_lock = threading.RLock()
        self._owner_thread_id = threading.get_ident()
        self.background = SessionBackground(self)
        self.locals = SessionLocals(self)
        self.execution = SessionExecution(self)
        self.operations = SessionOperations(self)
        self._observe()
        sys.modules[self._module_name] = self._module

    def _at_operator_boundary(self) -> bool:
        execution = self._active_execution
        if execution is None or execution._driver_thread_id != threading.get_ident():
            return False
        try:
            return execution._driver_task is asyncio.current_task()
        except RuntimeError:
            return False

    def _can_observe_live(self) -> bool:
        return not self._disposed and (
            self._at_operator_boundary() or self._active_execution is None and self._owner_thread_id == threading.get_ident()
        )

    def _observe(self) -> None:
        with self._namespace_lock:
            self._observation_revision += 1
            observed_at = time.time()
            try:
                self.locals._capture(observed_at, self._observation_revision)
            except Exception as error:  # noqa: BLE001 - Record observation or delivery failure without changing execution outcomes.
                self.locals._snapshot = replace(
                    self.locals._snapshot,
                    incomplete=True,
                    unavailable_reason=f"Fresh observation unavailable ({type(error).__name__}); retained values have their original observation time",
                )
            self.execution._capture(observed_at, self._observation_revision)

    def help(self, operation_name: str | None = None) -> tuple[OperationDescriptor, ...]:
        require_operator("session_help")
        return tuple(item for item in self.operations.list() if operation_name is None or item.name == operation_name)

    def exit(self) -> ExitResult:
        """Admit shutdown only after background preflight; refusal has no effects.

        An owning operator cell receives acceptance immediately and closes after
        unwind. An embedding application retains ownership of its own process.
        """

        require_operator("session_exit")
        if self._disposed:
            return ExitResult("closed")
        if self._exit_requested:
            return ExitResult("accepted")
        with self.background._lock:
            revision = self.background._revision
        blockers = self.background._blockers()
        if blockers:
            return ExitResult("refused", blockers, "Managed background work has not ceased")
        with self._namespace_lock, self.background._lock:
            if revision != self.background._revision:
                return ExitResult(
                    "refused",
                    (
                        BackgroundObservation(
                            "registration", "Session", "Background registration changed during preflight", "unknown", time.time(), False
                        ),
                    ),
                    "Retry exit after observing the new registration",
                )
            self._exit_requested = True
            self._closing = True
        if self._active_execution is not None:
            if not self._at_operator_boundary():
                self._active_execution.request_interrupt()
            return ExitResult("accepted")
        return ExitResult("closed" if self.close() else "accepted")

    def _require_open(self) -> None:
        if self._disposed:
            raise SessionClosedError("Session has been disposed")

    def classify_input(self, cell: CellInput) -> CellMode:
        self._require_open()
        if cell.mode is not None:
            if cell.mode not in ("python", "natural_language"):
                raise ValueError("unknown cell mode")
            return cell.mode
        return "python" if cell.text.startswith("> ") else "natural_language"

    def submit(self, cell: CellInput) -> CellExecution:
        require_operator("cell_submission")
        with self._namespace_lock:
            self._require_open()
            if self._closing:
                raise SessionClosedError("Session close is pending")
            if self._active_execution is not None:
                raise SessionBusyError("the preceding cell is unfinished")
            execution = CellExecution(self, cell)
            self._active_execution = execution
            self.history._append(cell_id=execution.cell_id, kind="input", text=cell.text)
            self._observe()
            return execution

    def _eligible(self, name: str, value: object) -> bool:
        if is_operator_control(value) or self.background._is_registered(value):
            return False
        if type(value) is MethodType and self.background._is_registered(value.__self__):
            return False
        if value is self.binding_name_to_value or issubclass(
            type(value), (SessionManager, Runner, ModuleType, SessionHistory, CellExecution, RunHandle, RunLedger, _Interpreter, _SourceRegistry)
        ):
            return False
        if _is_session_function(value):
            return value._owner() is self
        if issubclass(type(value), (GeneratedCallableReference, AsyncGeneratedCallableReference)):
            return False
        if callable(value):
            return self._sources.approval_for(value) is not None
        return not name.startswith("__")

    def expose(self, *binding_name_tuple: str) -> None:
        require_operator("session_exposure")
        self._require_open()
        for name in binding_name_tuple:
            if name not in self.binding_name_to_value or not self._eligible(name, self.binding_name_to_value[name]):
                raise SessionLookupError(f"binding cannot be exposed: {name}")
            value = self.binding_name_to_value[name]
            if isinstance(value, Capability) and value.name != name:
                raise SessionLookupError("capability binding must match its declared name")
        self._exposed_name_set.update(binding_name_tuple)

    def _selection(self, explicit_name_tuple: tuple[str, ...] = ()) -> tuple[Grant, dict[str, str]]:
        reference_name_to_value: dict[str, object] = {}
        capability_list: list[Capability] = []
        function_name_to_source_identity: dict[str, str] = {}
        for name, value in self.binding_name_to_value.items():
            if name.startswith("_") and name not in self._exposed_name_set and name not in explicit_name_tuple:
                continue
            if not self._eligible(name, value):
                continue
            if isinstance(value, Capability):
                if name != value.name:
                    raise SessionLookupError("capability binding must match its declared name")
                capability_list.append(value)
            else:
                reference_name_to_value[name] = value
                if _is_session_function(value):
                    function_name_to_source_identity[name] = value.definition.proposal.defining_source_identity
                elif callable(value):
                    function_name_to_source_identity[name] = self._sources.function_to_approval[value].source_identity
        selected = Grant.compose(self._grant, Grant(capability_list, reference_name_to_value))
        for name, value in self._grant.references.items():
            if _is_session_function(value):
                function_name_to_source_identity[name] = value.definition.proposal.defining_source_identity
                continue
            approval = self._sources.approval_for(value) if callable(value) else None
            if approval is not None:
                function_name_to_source_identity[name] = approval.source_identity
        return selected, function_name_to_source_identity

    def _inside_run(self, function: Callable[..., Any]) -> bool:
        host = current_host()
        if host is None:
            return False
        context = host.current_context()
        if host.ended or host.terminated or context is None or not host.is_node_live(context.node):
            raise CapabilityRejectedError(capability_name="function_call", reason_code="run_ended")
        if context.generated_source is None and not host.has_function_definition(function):
            self._sources.result_type(function)
        return True

    def _call_setup(self, function: Callable[..., Any]) -> tuple[Any, Grant, _CellRunHandle]:
        execution = self._active_execution
        if execution is None or self._disposed:
            raise SessionClosedError("Natural mediation has no active cell")
        if execution._interrupted:
            raise asyncio.CancelledError()
        execution._finish_pending()
        declaration = self._sources.result_type(function)
        grant, _ = self._selection()
        return declaration, grant, _CellRunHandle(execution)

    def _function_definitions(
        self, grant: Grant, explicit: tuple[Callable[..., object] | ApprovedFunctionDefinition, ...] = ()
    ) -> tuple[Callable[..., object] | ApprovedFunctionDefinition, ...]:
        """Supply observed approval associations; the Harness records them at entry."""
        definition_list: list[Callable[..., object] | ApprovedFunctionDefinition] = []
        recorded_function_id_set: set[int] = set()
        for item in (*explicit, *grant.references.values()):
            function = item.function if isinstance(item, ApprovedFunctionDefinition) else item
            if not callable(function) or id(function) in recorded_function_id_set:
                continue
            approval = self._sources.approval_for(function)
            if approval is not None:
                definition_list.append(
                    ApprovedFunctionDefinition(function, approval.source_identity, approval.approval_identity, approval.route, approval.cell_id)
                )
            elif any(item is definition for definition in explicit):
                definition_list.append(item if isinstance(item, ApprovedFunctionDefinition) else function)
            else:
                continue
            recorded_function_id_set.add(id(function))
        return tuple(definition_list)

    def invoke(self, call: FunctionCall, proceed: Callable[[], Any]) -> Any:
        function = call.definition.function
        if self._inside_run(function):
            return proceed()
        declaration, grant, handle = self._call_setup(function)
        return self._runner.run(
            ScaffoldComposition(
                RunContract(type(None), declaration),
                lambda value: proceed(),
                run_entry_grant=grant,
                composition_perimeter_policy=self._policy,
                function_definition_tuple=self._function_definitions(grant, (function,)),
            ),
            None,
            handle=handle,
        )

    async def invoke_async(self, call: FunctionCall, proceed: Callable[[], Awaitable[Any]]) -> Any:
        function = call.definition.function
        if self._inside_run(function):
            return await proceed()
        declaration, grant, handle = self._call_setup(function)
        return await self._runner.run(
            AsyncScaffoldComposition(
                RunContract(type(None), declaration),
                lambda value: proceed(),
                run_entry_grant=grant,
                composition_perimeter_policy=self._policy,
                function_definition_tuple=self._function_definitions(grant, (function,)),
            ),
            None,
            handle=handle,
        )

    def _generated_call_setup(self) -> tuple[Grant, _CellRunHandle]:
        self._require_open()
        execution = self._active_execution
        if execution is None or not self._at_operator_boundary():
            raise SessionLookupError("Call a Session function from an operator Python cell or an active agent run")
        if execution._interrupted:
            raise asyncio.CancelledError()
        execution._finish_pending()
        grant, _ = self._selection()
        return grant, _CellRunHandle(execution)

    def _invoke_generated(
        self, function: SessionFunction, positional_arguments: tuple[object, ...], keyword_argument_name_to_value: dict[str, object]
    ) -> object:
        if current_host() is not None:
            return function._reference()(*positional_arguments, **keyword_argument_name_to_value)
        grant, handle = self._generated_call_setup()
        return self._runner.run(
            ScaffoldComposition(
                RunContract(type(None), object),
                lambda value: function._reference()(*positional_arguments, **keyword_argument_name_to_value),
                run_entry_grant=grant,
                composition_perimeter_policy=self._policy,
                function_definition_tuple=self._function_definitions(grant),
            ),
            None,
            handle=handle,
        )

    async def _invoke_generated_async(
        self, function: SessionFunction, positional_arguments: tuple[object, ...], keyword_argument_name_to_value: dict[str, object]
    ) -> object:
        self._require_open()

        async def invoke(value: None) -> object:
            reference = function._reference()
            if not isinstance(reference, AsyncGeneratedCallableReference):
                raise SessionLookupError("The declared function is not asynchronous")
            return await reference(*positional_arguments, **keyword_argument_name_to_value)

        if current_host() is not None:
            return await invoke(None)
        grant, handle = self._generated_call_setup()
        return await self._runner.run(
            AsyncScaffoldComposition(
                RunContract(type(None), object),
                invoke,
                run_entry_grant=grant,
                composition_perimeter_policy=self._policy,
                function_definition_tuple=self._function_definitions(grant),
            ),
            None,
            handle=handle,
        )

    async def _conversation(self, execution: CellExecution, task: str) -> None:
        bindings = _SessionBindings.prepare(task, self.binding_name_to_value)
        bindings.declare_function = lambda request: _declare_function(self, request)
        candidates, function_name_to_source_identity = self._selection((*bindings.read_name_tuple, *bindings.write_name_tuple))
        selection = _QuerySelection(candidates, function_name_to_source_identity, self.history, self.history._count)
        ceiling = Grant.compose(candidates, Grant(selection.capability_tuple))
        composition = self._builder(ceiling, self._policy)
        if not isinstance(composition, AsyncScaffoldComposition) or composition.run_contract != RunContract(ConversationInput, ConversationResult):
            raise RunRejectedError(reason="invalid_declaration", detail="conversation builder must preserve the async input/result contract")
        grant = composition.run_entry_grant
        if grant.widening_over(ceiling) or any(
            name not in ceiling.references or ceiling.references[name] is not value for name, value in grant.references.items()
        ):
            raise RunRejectedError(reason="invalid_declaration", detail="conversation builder widened its grant")
        if composition.composition_perimeter_policy.excess_over(self._policy):
            raise RunRejectedError(reason="perimeter_policy_widening", detail="conversation builder widened its policy")
        selection.final_grant = grant
        bindings.require_reads(grant)
        conversation_input = ConversationInput(
            task,
            selection.query_data(ContextQuery()),
            selection.query_functions(ContextQuery()),
            selection.recent_history(),
            bindings.read_name_tuple,
            bindings.write_name_tuple,
        )
        handle = _CellRunHandle(execution)

        async def converse(value: ConversationInput) -> ConversationResult:
            result = await composition.body(value)
            assert handle.run_id is not None
            bindings.finalize(result, handle.run_id, execution.cell_id)
            return replace(result, binding_name_to_value=dict(bindings.pending_name_to_value))

        with _binding_scope(bindings):
            result = await self._runner.run(
                replace(
                    composition, body=converse, function_definition_tuple=self._function_definitions(grant, composition.function_definition_tuple)
                ),
                conversation_input,
                handle=handle,
            )
        for block in result.block_tuple:
            for reference in block.evidence_reference_tuple:
                ledger = self._run_id_to_ledger.get(reference.run_id)
                if ledger is None:
                    raise SessionLookupError("conversation cited unavailable run evidence")
                try:
                    resolve_evidence(ledger, reference)
                except ValueError as error:
                    raise SessionLookupError("conversation cited unavailable run evidence or value path") from error
            execution._record(block.kind, block.text, run_id=handle.run_id, evidence_reference_tuple=block.evidence_reference_tuple)
        self.binding_name_to_value.update(result.binding_name_to_value)
        for name, value in result.binding_name_to_value.items():
            action = "defined" if _is_session_function(value) else "retained"
            execution._record("publication", f"{action} {name}", run_id=handle.run_id, event_id=handle.ledger.select(RunEndEvent)[0].event_id)

    def read_run_evidence(
        self,
        run_id: str,
        request: EvidenceQuery = _DEFAULT_HISTORY_QUERY,
        *,
        bounds: EvidenceBounds = _DEFAULT_HISTORY_BOUNDS,
    ) -> EvidenceProjection:
        require_operator("run_evidence_observation")
        self._require_open()
        ledger = self._run_id_to_ledger.get(run_id)
        if ledger is None:
            raise SessionLookupError(f"unknown Session run: {run_id}")
        return project_evidence(ledger.entries, request, run_id=run_id, bounds=bounds)

    def close(self) -> bool:
        require_operator("session_close")
        if self._disposed:
            return True
        with self._namespace_lock:
            self._closing = True
        execution = self._active_execution
        if execution is not None:
            execution.request_interrupt()
            if execution.outcome is None:
                if not execution._body_unwound or execution._driver_task is not None:
                    return False
                try:
                    execution._finish_pending()
                except (RunTerminationError, SessionWaitContextError):
                    return False
                execution._finish("cancelled")
        for blocker in self.background._blockers():
            self.background.interrupt(blocker.work_id)
        if self.background._blockers():
            return False
        self._disposed = True
        self._interpreter.dispose()
        self._sources.dispose()
        self.history._dispose()
        self._run_id_to_ledger.clear()
        if sys.modules.get(self._module_name) is self._module:
            del sys.modules[self._module_name]
        self._module = None
        self.binding_name_to_value = {}
        self._observe()
        return True


def create_session(*, execution_model: str, decision_model: str | None = None) -> Session:
    """Create a finite configuration permitting exactly the selected models, without constructing a provider.

    Without ``decision_model``, ``decide`` blocks fail.
    """

    require_operator("session_creation")
    runner = model_runner(
        model_configuration_tuple=(
            StepExecutorConfiguration(
                execution_model=execution_model,
                decision_model=decision_model,
                execution_model_settings={"max_tokens": 4000},
            ),
        ),
        default_execution_model=execution_model,
        default_decision_model=decision_model,
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(
            maximum_recursion_depth=8,
            maximum_dispatch_count=24,
            maximum_natural_execution_count=24,
            maximum_executor_step_count=24,
            maximum_total_token_count=60000,
        ),
    )
    return SessionManager(runner=runner).create_session()
