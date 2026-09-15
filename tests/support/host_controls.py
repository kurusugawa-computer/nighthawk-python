"""Host-policy fixtures for runtime and provider integration tests."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
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
    """Test accounting policy with records separate from UsageMeter.

    Pydantic AI collapses absent usage to zeros.
    Treat an all-zero total as unknown conservatively.
    """

    def __init__(self, threshold: int = 100, host_unit: str = "test") -> None:
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
