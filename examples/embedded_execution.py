"""Core-only host integration; run directly or from an installed wheel."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapModelRequestHandler
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

import nighthawk as nh
from nighthawk.lifecycle import StepFinished, StepLifecycle
from nighthawk.runtime import StepCandidate, StepRequest


class RequestRefusedError(RuntimeError):
    """The host's already observed usage reached its admission threshold."""


@dataclass
class RequestObservation:
    request_id: int
    host_unit: str
    call_id: str | None
    step_execution_id: str | None
    configured_model: str
    reported_model: str | None = None
    tokens: int | None = None
    status: Literal["running", "succeeded", "failed", "interrupted", "refused"] = "running"


class RequestObserver(AbstractCapability[nh.StepContext]):
    """Example approximate policy; one record owner, separate from UsageMeter.

    Pydantic AI collapses absent usage to zeros. Treat an all-zero total as
    unknown conservatively; an adapter with stronger evidence may distinguish it.
    """

    def __init__(self, threshold: int = 100, host_unit: str = "example") -> None:
        super().__init__()
        self.threshold = threshold
        self.host_unit = host_unit
        self.observed_tokens = 0
        self.records: list[RequestObservation] = []
        self.lock = threading.Lock()

    # Retain the dependency's keyword-compatible parameter name on this override.
    async def wrap_model_request(
        self, ctx: RunContext[nh.StepContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        call = nh.get_function_call()
        with self.lock:
            record = RequestObservation(
                len(self.records) + 1,
                self.host_unit,
                call.call_id if call else None,
                nh.get_execution_reference().step_execution_id,
                request_context.model.model_name,
            )
            self.records.append(record)
            if self.observed_tokens >= self.threshold:
                record.status = "refused"
                raise RequestRefusedError("Observed request budget reached")
        try:
            response = await handler(request_context)
        except BaseException as exception:
            record.status = "failed" if isinstance(exception, Exception) else "interrupted"
            raise
        with self.lock:
            record.reported_model = response.model_name
            record.tokens = response.usage.total_tokens or None
            record.status = "succeeded"
            if record.tokens is not None:
                self.observed_tokens += record.tokens
        return response


class RecordingHandler:
    def __init__(self) -> None:
        self.calls: list[nh.FunctionCall] = []
        self.outcomes: list[str] = []
        self.refuse = False

    def accept(self, call: nh.FunctionCall) -> None:
        self.calls.append(call)
        if self.refuse:
            self.outcomes.append("refused")
            raise PermissionError("Host refused invocation")

    def invoke(self, call: nh.FunctionCall, proceed: Callable[[], object]) -> object:
        self.accept(call)
        try:
            result = proceed()
        except BaseException:
            self.outcomes.append("failed")
            raise
        self.outcomes.append("returned")
        return result

    async def invoke_async(self, call: nh.FunctionCall, proceed: Callable[[], Awaitable[object]]) -> object:
        self.accept(call)
        try:
            result = await proceed()
        except BaseException:
            self.outcomes.append("failed")
            raise
        self.outcomes.append("returned")
        return result


class PreservingExecutor:
    """An application wrapper that resolves allowed models without losing itself."""

    def __init__(self, executor: nh.AgentStepExecutor, model_name_to_model: dict[str, FunctionModel], observer: RequestObserver) -> None:
        self.executor = executor
        self.model_name_to_model = model_name_to_model
        self.observer = observer

    def resolve_model_selection(self, selection: nh.ModelSelection) -> PreservingExecutor:
        configuration = selection.apply(self.executor.configuration)
        model = configuration.model
        if isinstance(model, str):
            if model not in self.model_name_to_model:
                raise PermissionError("Model selection is not allowed")
            configuration = configuration.model_copy(update={"model": self.model_name_to_model[model]})
        elif not any(model is allowed for allowed in self.model_name_to_model.values()):
            raise PermissionError("Borrowed model is not allowed")
        return PreservingExecutor(nh.AgentStepExecutor.from_configuration(configuration=configuration), self.model_name_to_model, self.observer)

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        assert self.observer in nh.get_capabilities()
        return self.executor.run_step(request=request)

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        assert self.observer in nh.get_capabilities()
        return await self.executor.run_step_async(request=request)


def deterministic_model(name: str, tokens: int = 60) -> FunctionModel:
    async def respond(messages: list[ModelMessage], information: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}})],
            model_name=name,
            usage=RequestUsage(input_tokens=tokens - 10, output_tokens=10),
        )

    return FunctionModel(respond, model_name=name)


@nh.natural_function
def block() -> None:
    """natural
    Complete this block without changing anything.
    """


@nh.natural_function(model="allowed:B")
async def child() -> None:
    """natural
    Complete this child block without changing anything.
    """


async def demonstrate_cleanup() -> bool:
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    completed: list[str] = []

    async def notification(event: StepFinished) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()
            completed.append("notification")

    @nh.natural_function
    async def interrupted() -> None:
        """natural
        Complete this block without changing anything.
        """

    with nh.scope(lifecycle=StepLifecycle(on_step_finished=notification)):
        task = asyncio.create_task(interrupted())
        try:
            await entered.wait()
            task.cancel()
            await cleanup.wait()
            assert not task.done()
        finally:
            release.set()
        with suppress(asyncio.CancelledError):
            await task
    assert completed == ["notification"]
    return True


def main() -> None:
    model_name_to_model = {"allowed:A": deterministic_model("A"), "allowed:B": deterministic_model("B")}
    handler = RecordingHandler()
    observer = RequestObserver(threshold=10_000)
    executor = PreservingExecutor(
        nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=model_name_to_model["allowed:A"])),
        model_name_to_model,
        observer,
    )
    effects: list[str] = []

    @nh.natural_function(model="allowed:B")
    def refused() -> None:
        effects.append("entered")

    with nh.function_call_scope(handler), nh.run(executor), nh.scope(capabilities=[observer]):
        handler.refuse = True
        with suppress(PermissionError):
            refused()
        handler.refuse = False
        assert not effects
        print(f"refused_body_effects={len(effects)}")
        block()
        asyncio.run(child())
        block()
        models = [record.configured_model for record in observer.records]
        assert models == ["A", "B", "A"]
        print("models=" + ",".join(models))
        completed = asyncio.run(demonstrate_cleanup())

    budget = RequestObserver()
    executor = PreservingExecutor(executor.executor, model_name_to_model, budget)
    with nh.function_call_scope(handler), nh.run(executor), nh.scope(capabilities=[budget]):
        block()
        asyncio.run(child())
        try:
            block()
        except nh.ExecutionError as exception:
            assert isinstance(exception.__cause__, RequestRefusedError)
        else:
            raise AssertionError("Third request was not refused")
    assert budget.observed_tokens == 120
    assert [record.status for record in budget.records] == ["succeeded", "succeeded", "refused"]
    assert all(record.call_id and record.step_execution_id for record in budget.records)
    print(f"observed_tokens={budget.observed_tokens}")
    print("next_request_refused=True")
    print(f"returned_after_notification_cleanup={completed}")


if __name__ == "__main__":
    main()
