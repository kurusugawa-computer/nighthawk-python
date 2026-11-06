"""Executor contracts independent of the agent implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from nighthawk.core.model_selection import ModelSelection
from nighthawk.core.step_request import StepCandidate, StepRequest


@runtime_checkable
class SyncStepExecutor(Protocol):
    """Step executor that provides synchronous execution."""

    def run_step(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate: ...


@runtime_checkable
class AsyncStepExecutor(Protocol):
    """Step executor that provides asynchronous execution."""

    async def run_step_async(
        self,
        *,
        request: StepRequest,
    ) -> StepCandidate: ...


type StepExecutor = SyncStepExecutor | AsyncStepExecutor


@runtime_checkable
class ConfigurableStepExecutor(Protocol):
    """Optional resolution owned by the active executor, including its host controls."""

    def resolve_model_selection(self, selection: ModelSelection) -> StepExecutor: ...
