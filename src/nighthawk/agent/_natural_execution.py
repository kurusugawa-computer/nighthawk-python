"""Host-mediated step executor wrapping the platform's executor.

Every Natural execution of an agent run passes through this wrapper: it admits
the execution against the resource allocation, checks the projected grant,
delegates the executor steps to the wrapped executor, validates the complete
declared write set, and records exactly one typed outcome event.

The following boundaries keep the core machinery inside host mediation:

- The host admits the execution at the executor seam and settles its steps
  there; the core validates the write set once, presents it to the host's
  commit inspector before assignment, and delivers the terminal record to the
  host's step lifecycle after assignment, where exactly one outcome event is
  recorded and failures become the host's typed exception.
- The core ``return`` outcome is never offered to the delegate. Within an agent run a Natural block
  returns its judgment through declared write bindings only, and the
  deterministic Python of the containing Natural function decides what to
  return. A ``decide`` block permits only ``pass`` and passes through unchanged.
- Executor steps are observed as decided at run entry
  (``nighthawk.agent.runtime.ExecutorStepObservation``). When the delegate is
  the core ``AgentStepExecutor`` whose execute agent is a Pydantic AI agent or
  not yet selected, the host installs a Pydantic AI capability for the
  execution. The core passes scoped capabilities to execute agent runs and
  decide model runs alike, so each executor step of either block kind is
  presented for admission before its LLM request and its response usage is
  accounted immediately; allocation exhaustion surfaces at the step that would
  exceed it. An executor that admits its own steps calls the host admission itself.
  An executor the platform accepted in aggregate mode has its steps admitted as
  soon as the host learns of them, and an over-consuming execution ends in a
  typed ``allocation_exhausted`` failure instead of a success.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapModelRequestHandler

import nighthawk as nh
from nighthawk.configuration import ModelSelection
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.step_executor import StepExecutor
from nighthawk.runtime.step_outcome import OutcomeKind
from nighthawk.runtime.step_request import StepCandidate, StepRequest

from ._audit import host_service_scope
from ._host import HOST_OWNED_ATTRIBUTE_NAME, Host, _NaturalExecutionRecord
from .runtime import ExecutorStepObservation

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models import ModelRequestContext
    from pydantic_ai.tools import RunContext

__all__ = ["HostStepExecutor", "executor_step_observation_of"]


_HOST_REJECTED_OUTCOME_KIND_TUPLE: tuple[OutcomeKind, ...] = ("return",)

type _RunStep = Callable[..., StepCandidate]
type _RunStepAsync = Callable[..., Coroutine[Any, Any, StepCandidate]]


def _observes_model_requests(step_executor: object) -> bool:
    """Whether every model request of ``step_executor`` passes through the capabilities of the current core scope.

    The core ``AgentStepExecutor`` passes them to its Pydantic AI execute agent and to every decide model run.
    A borrowed execute agent of another kind is not observable.
    """

    return isinstance(step_executor, nh.AgentStepExecutor) and (step_executor.agent is None or isinstance(step_executor.agent, Agent))


def executor_step_observation_of(step_executor: object) -> ExecutorStepObservation:
    """Decide how the host observes the executor steps of ``step_executor``."""

    if _observes_model_requests(step_executor):
        return "model_requests"
    if getattr(step_executor, "admits_executor_steps", False) is True:
        return "executor_reports"
    return "aggregate"


def _run_coroutine_synchronously[R](coroutine_call: Callable[[], Coroutine[Any, Any, R]]) -> R:
    """Use the core bridge, which confirms worker completion before propagating interruption."""

    return cast(R, run_coroutine_synchronously(coroutine_call))


class _ExecutorStepCapability(AbstractCapability[nh.StepContext]):
    """Pydantic AI capability that presents each model request of one Natural execution for admission.

    Installed by the host for the duration of the execution through
    ``nighthawk.scope(capabilities=...)``; the core ``AgentStepExecutor`` passes
    it to every ``Agent.run``. Requests, streamed or not, are admitted before
    they are sent and their response usage is accounted immediately.
    """

    def __init__(self, host: Host, record: _NaturalExecutionRecord) -> None:
        self.id = "nighthawk_agent_executor_step_admission"
        self._host = host
        self._record = record

    async def wrap_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, context: RunContext[nh.StepContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        with host_service_scope():
            return await handler(request_context)

    async def before_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, context: RunContext[nh.StepContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        self._host.admit_executor_step(self._record)
        return request_context

    async def after_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, context: RunContext[nh.StepContext], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        self._host.observe_executor_step(self._record, response.usage, model_name=response.model_name)
        return response


setattr(_ExecutorStepCapability, HOST_OWNED_ATTRIBUTE_NAME, True)


class HostStepExecutor:
    """Step executor that routes every Natural execution through host mediation."""

    def __init__(self, host: Host, delegate: StepExecutor) -> None:
        self._host = host
        self._delegate = delegate

    @property
    def delegate(self) -> StepExecutor:
        return self._delegate

    def resolve_model_selection(self, selection: ModelSelection) -> HostStepExecutor:
        """Select only through the configured delegate, preserving this run's host."""

        configure = getattr(self._delegate, "resolve_model_selection", None)
        if not callable(configure):
            raise TypeError("this Runner does not support model selection; configure Runner.from_models")
        return HostStepExecutor(self._host, cast(StepExecutor, configure(selection)))

    @property
    def tool_result_rendering_policy(self) -> object:
        return getattr(self._delegate, "tool_result_rendering_policy", None)

    def _accepted_outcome_kinds(self, record: _NaturalExecutionRecord, allowed_outcome_kinds: tuple[OutcomeKind, ...]) -> tuple[OutcomeKind, ...]:
        accepted: tuple[OutcomeKind, ...] = tuple(kind for kind in allowed_outcome_kinds if kind not in _HOST_REJECTED_OUTCOME_KIND_TUPLE)
        if not accepted:
            raise self._host.fail_natural_execution(
                record,
                "disallowed_outcome_kind",
                "the block permits only outcome kinds the host does not accept inside an agent run",
            )
        return accepted

    @contextmanager
    def _observed_model_requests(self, record: _NaturalExecutionRecord) -> Generator[None]:
        """Install the host capability that admits and accounts each model request under the ``model_requests`` observation mode."""

        if self._host.executor_step_observation != "model_requests" or not _observes_model_requests(self._delegate):
            yield
            return
        with nh.scope(capabilities=[_ExecutorStepCapability(self._host, record)]):
            yield

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        record = self._host.begin_natural_execution(request)
        accepted_outcome_kinds = self._accepted_outcome_kinds(record, request.allowed_outcome_kinds)
        selected_request = request.model_copy(update={"allowed_outcome_kinds": accepted_outcome_kinds})
        with self._host.natural_execution_context(record), self._observed_model_requests(record):
            run_step = cast(_RunStep | None, getattr(self._delegate, "run_step", None))
            if callable(run_step):
                candidate = run_step(request=selected_request)
            else:
                run_step_async = cast(_RunStepAsync, getattr(self._delegate, "run_step_async"))  # noqa: B009
                candidate = _run_coroutine_synchronously(lambda: run_step_async(request=selected_request))
        self._host.settle_execution(record, candidate.outcome, allowed_outcome_kinds=accepted_outcome_kinds)
        return candidate

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        record = self._host.begin_natural_execution(request)
        accepted_outcome_kinds = self._accepted_outcome_kinds(record, request.allowed_outcome_kinds)
        selected_request = request.model_copy(update={"allowed_outcome_kinds": accepted_outcome_kinds})
        with self._host.natural_execution_context(record), self._observed_model_requests(record):
            run_step_async = cast(_RunStepAsync | None, getattr(self._delegate, "run_step_async", None))
            if callable(run_step_async):
                candidate = await run_step_async(request=selected_request)
            else:
                run_step = cast(_RunStep, getattr(self._delegate, "run_step"))  # noqa: B009
                candidate = run_step(request=selected_request)
        self._host.settle_execution(record, candidate.outcome, allowed_outcome_kinds=accepted_outcome_kinds)
        return candidate
