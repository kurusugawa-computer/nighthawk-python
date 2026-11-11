"""Lazy provider construction with host admission of every model request."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapModelRequestHandler
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestContext

from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.adapters.pydantic_ai.scoping import model_scope
from nighthawk.adapters.pydantic_ai.usage import execution_usage
from nighthawk.core.async_bridge import run_coroutine_synchronously
from nighthawk.core.step_context import StepContext
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.harness._audit import host_service_scope
from nighthawk.harness._host import HOST_OWNED_ATTRIBUTE_NAME
from nighthawk.harness.context import current_executor_step_admission

type ModelExchangeObserver = Callable[[Sequence[ModelMessage], ModelResponse], None]


class _RequestReporting(AbstractCapability[StepContext]):
    def __init__(self, exchange_observer: ModelExchangeObserver | None = None) -> None:
        self._exchange_observer = exchange_observer

    async def wrap_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, context: RunContext[StepContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        with host_service_scope():
            return await handler(request_context)

    async def before_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        context: RunContext[StepContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        current_executor_step_admission().admit()
        return request_context

    async def after_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        context: RunContext[StepContext],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        current_executor_step_admission().observe(execution_usage(response.usage), model_name=response.model_name)
        if self._exchange_observer is not None:
            # The observer is trusted host code, so what it records is host infrastructure rather than an effect of the step.
            with host_service_scope():
                self._exchange_observer(tuple(request_context.messages), response)
        return response


setattr(_RequestReporting, HOST_OWNED_ATTRIBUTE_NAME, True)


def observes_model_requests(step_executor: object) -> bool:
    return isinstance(step_executor, AgentStepExecutor) and (step_executor.agent is None or isinstance(step_executor.agent, Agent))


@contextmanager
def model_request_scope(exchange_observer: ModelExchangeObserver | None = None) -> Generator[None]:
    with model_scope(capabilities=[_RequestReporting(exchange_observer)]):
        yield


class _LazyExecutor:
    """Construct delegates on first use, one for each event loop running steps concurrently.

    A provider's HTTP client belongs to the event loop that opened it and is not moved while that loop is still running.
    A synchronous Natural function called from an asynchronous step runs on a bridge loop while the caller's loop waits,
    so it receives another delegate. A delegate last used on a loop that is no longer running is reused.
    """

    admits_executor_steps = True

    def __init__(self, factory: Callable[[], AgentStepExecutor], exchange_observer: ModelExchangeObserver | None = None) -> None:
        self._factory = factory
        self._exchange_observer = exchange_observer
        self._lock = threading.Lock()
        self._delegate_and_loop_list: list[tuple[AgentStepExecutor, asyncio.AbstractEventLoop]] = []

    def _delegate_for(self, loop: asyncio.AbstractEventLoop) -> AgentStepExecutor:
        with self._lock:
            for index, (delegate, delegate_loop) in enumerate(self._delegate_and_loop_list):
                if delegate_loop is loop or not delegate_loop.is_running():
                    self._delegate_and_loop_list[index] = (delegate, loop)
                    return delegate
            with host_service_scope():
                delegate = self._factory()
            self._delegate_and_loop_list.append((delegate, loop))
            return delegate

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        delegate = self._delegate_for(asyncio.get_running_loop())
        with model_request_scope(self._exchange_observer):
            return await delegate.run_step_async(request=request)

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        return run_coroutine_synchronously(lambda: self.run_step_async(request=request))
