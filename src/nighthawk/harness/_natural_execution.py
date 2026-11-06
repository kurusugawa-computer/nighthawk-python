"""Mediate Natural execution, declared writes, and terminal outcomes under a host grant.

Model request observation belongs to the selected adapter; the host settles usage and records the accepted outcome.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Generator
from contextlib import contextmanager
from typing import Any, cast

from nighthawk.adapters.pydantic_ai._harness_executor import model_request_scope, observes_model_requests
from nighthawk.core.async_bridge import run_coroutine_synchronously
from nighthawk.core.model_selection import ModelSelection
from nighthawk.core.step_executor import StepExecutor
from nighthawk.core.step_outcome import OutcomeKind
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.harness._host import Host, _NaturalExecutionRecord
from nighthawk.harness.ledger import ExecutorStepObservation

__all__ = ["HostStepExecutor", "executor_step_observation_of"]


_HOST_REJECTED_OUTCOME_KIND_TUPLE: tuple[OutcomeKind, ...] = ("return",)

type _RunStep = Callable[..., StepCandidate]
type _RunStepAsync = Callable[..., Coroutine[Any, Any, StepCandidate]]


def executor_step_observation_of(step_executor: object) -> ExecutorStepObservation:
    """Decide how the host observes the executor steps of ``step_executor``."""

    if observes_model_requests(step_executor):
        return "model_requests"
    if getattr(step_executor, "admits_executor_steps", False) is True:
        return "executor_reports"
    return "aggregate"


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
            raise TypeError("this Runner does not support model selection; configure model_runner")
        return HostStepExecutor(self._host, cast(StepExecutor, configure(selection)))

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
    def _observed_model_requests(self) -> Generator[None]:
        """Install the host capability that admits and accounts each model request under the ``model_requests`` observation mode."""

        if self._host.executor_step_observation != "model_requests" or not observes_model_requests(self._delegate):
            yield
            return
        with model_request_scope():
            yield

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        record = self._host.begin_natural_execution(request)
        accepted_outcome_kinds = self._accepted_outcome_kinds(record, request.allowed_outcome_kinds)
        selected_request = request.model_copy(update={"allowed_outcome_kinds": accepted_outcome_kinds})
        with self._host.natural_execution_context(record), self._observed_model_requests():
            run_step = cast(_RunStep | None, getattr(self._delegate, "run_step", None))
            if callable(run_step):
                candidate = run_step(request=selected_request)
            else:
                run_step_async = cast(_RunStepAsync, getattr(self._delegate, "run_step_async"))  # noqa: B009
                candidate = cast(StepCandidate, run_coroutine_synchronously(lambda: run_step_async(request=selected_request)))
        self._host.settle_execution(record, candidate.outcome, allowed_outcome_kinds=accepted_outcome_kinds)
        return candidate

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        record = self._host.begin_natural_execution(request)
        accepted_outcome_kinds = self._accepted_outcome_kinds(record, request.allowed_outcome_kinds)
        selected_request = request.model_copy(update={"allowed_outcome_kinds": accepted_outcome_kinds})
        with self._host.natural_execution_context(record), self._observed_model_requests():
            run_step_async = cast(_RunStepAsync | None, getattr(self._delegate, "run_step_async", None))
            if callable(run_step_async):
                candidate = await run_step_async(request=selected_request)
            else:
                run_step = cast(_RunStep, getattr(self._delegate, "run_step"))  # noqa: B009
                candidate = run_step(request=selected_request)
        self._host.settle_execution(record, candidate.outcome, allowed_outcome_kinds=accepted_outcome_kinds)
        return candidate
