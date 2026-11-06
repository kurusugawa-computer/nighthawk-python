"""Deterministic executors for testing the Python around Natural functions without model calls.

Both executors return ordinary ``StepCandidate`` values and record each live ``StepRequest``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from nighthawk.core.async_bridge import run_awaitable_value_synchronously
from nighthawk.core.step_request import StepCandidate, StepRequest


class ScriptedExecutor:
    """Return scripted candidates in order and record every request.

    Once the sequence is exhausted, ``default_response`` answers later requests.
    Without one, the unexpected request is recorded and raises AssertionError;
    ordinary runtime execution wraps it in ExecutionError with failure stage ``executor``.
    """

    def __init__(self, responses: Sequence[StepCandidate] = (), *, default_response: StepCandidate | None = None) -> None:
        self.responses: tuple[StepCandidate, ...] = tuple(responses)
        self.default_response = default_response
        self.requests: list[StepRequest] = []

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        self.requests.append(request)
        index = len(self.requests) - 1
        response = self.responses[index] if index < len(self.responses) else self.default_response
        if response is None:
            raise AssertionError(
                f"ScriptedExecutor has no response for request {index + 1}; "
                f"{len(self.responses)} responses were supplied. "
                "Supply another response or an explicit default_response."
            )
        return response

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        return self.run_step(request=request)


class CallbackExecutor:
    """Answer each request with ``handler(request)`` and record every request.

    The handler returns a StepCandidate directly or an awaitable resolving to one.
    Synchronous execution calls the handler on the caller thread and joins any returned awaitable through the runtime bridge.
    Asynchronous execution awaits it in the caller's task.
    Handler failures and cancellation propagate through the ordinary step lifecycle.
    """

    def __init__(self, handler: Callable[[StepRequest], StepCandidate | Awaitable[StepCandidate]]) -> None:
        self.handler = handler
        self.requests: list[StepRequest] = []

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        self.requests.append(request)
        return cast(StepCandidate, run_awaitable_value_synchronously(self.handler(request)))

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        self.requests.append(request)
        response = self.handler(request)
        return await response if inspect.isawaitable(response) else response
