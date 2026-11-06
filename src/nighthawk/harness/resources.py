from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from nighthawk._identifiers import generate_ulid
from nighthawk.core.usage import ExecutionUsage

type ResourceRequestKind = Literal["dispatch", "natural_execution", "executor_step"]


class ResourceRequest(BaseModel):
    """One resource-consuming request presented for admission.

    ``dispatch`` admits one dispatch node, ``natural_execution`` admits one
    Natural execution before its first executor step, and ``executor_step``
    admits one executor step (one LLM response) of an admitted Natural
    execution. The host presents an executor step before its LLM request under
    the ``model_requests`` and ``executor_reports`` observation modes of
    :data:`ExecutorStepObservation`, and otherwise as soon as it learns of the
    step from the executor's metered usage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    kind: ResourceRequestKind
    attribution_node_id: str
    recursion_depth: int = Field(ge=0)


class ResourceRejection(BaseModel):
    """Typed refusal of a resource request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str


class ResourceConsumption(BaseModel):
    """Consumption reported by the host after it metered it.

    An ``executor_step`` consumption reports one observed LLM response as soon
    as the host sees it, so the next step admission can account for it. A
    ``natural_execution`` consumption reports what the execution consumed
    beyond the steps already reported; ``executor_step_count`` repeats the
    number of steps the host presented for admission.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    kind: ResourceRequestKind
    attribution_node_id: str
    recursion_depth: int = Field(default=0, ge=0)
    executor_step_count: int = Field(default=0, ge=0)
    executor_usage: ExecutionUsage = ExecutionUsage()


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


@runtime_checkable
class ExecutorStepAdmission(Protocol):
    """Host-side admission of the executor steps of the current Natural execution.

    A step executor that declares ``admits_executor_steps = True`` obtains this
    through :func:`nighthawk.harness.context.current_executor_step_admission` and uses
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

    def observe(self, usage: ExecutionUsage | None = None, *, model_name: str | None = None) -> None: ...


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
