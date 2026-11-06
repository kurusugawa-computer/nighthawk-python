from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, overload

from nighthawk.core.step_executor import StepExecutor
from nighthawk.harness._run import _run_async, _run_sync
from nighthawk.harness.audit import PerimeterAuditStore
from nighthawk.harness.confinement import PerimeterConfinement
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import ResourceAllocation
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunIdentity, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.harness.termination import TerminationMechanism, unavailable_termination_mechanism


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
    Natural call inside a running asynchronous step requires the adapter's ``model_runner`` factory, which constructs a delegate per such loop.
    """

    step_executor: StepExecutor
    perimeter_policy: PerimeterPolicy
    resource_allocation_factory: Callable[[RunIdentity], ResourceAllocation]
    perimeter_confinement: PerimeterConfinement | None = None
    perimeter_audit_store: PerimeterAuditStore | None = None
    termination_mechanism: TerminationMechanism = field(default_factory=unavailable_termination_mechanism)
    accepts_aggregate_executor_accounting: bool = False

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
