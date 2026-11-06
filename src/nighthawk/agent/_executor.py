"""Lazy provider construction with host admission of every model request."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapModelRequestHandler
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext

import nighthawk as nh
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.step_request import StepCandidate, StepRequest

from ._audit import host_service_scope
from ._host import HOST_OWNED_ATTRIBUTE_NAME, current_executor_step_admission


class _RequestReporting(AbstractCapability[nh.StepContext]):
    async def wrap_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, context: RunContext[nh.StepContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        with host_service_scope():
            return await handler(request_context)

    async def before_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        context: RunContext[nh.StepContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        current_executor_step_admission().admit()
        return request_context

    async def after_model_request(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        context: RunContext[nh.StepContext],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        current_executor_step_admission().observe(response.usage, model_name=response.model_name)
        return response


setattr(_RequestReporting, HOST_OWNED_ATTRIBUTE_NAME, True)


class _LazyExecutor:
    """Construct delegates on first use, one for each event loop running steps concurrently.

    A provider's HTTP client belongs to the event loop that opened it and is not moved while that loop is still running.
    A synchronous Natural function called from an asynchronous step runs on a bridge loop while the caller's loop waits,
    so it receives another delegate. A delegate last used on a loop that is no longer running is reused.
    """

    admits_executor_steps = True

    def __init__(self, factory: Callable[[], nh.AgentStepExecutor]) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._delegate_and_loop_list: list[tuple[nh.AgentStepExecutor, asyncio.AbstractEventLoop]] = []

    def _delegate_for(self, loop: asyncio.AbstractEventLoop) -> nh.AgentStepExecutor:
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
        with nh.scope(capabilities=[_RequestReporting()]):
            return await delegate.run_step_async(request=request)

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        return run_coroutine_synchronously(lambda: self.run_step_async(request=request))
