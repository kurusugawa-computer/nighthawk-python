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
from nighthawk.adapters.pydantic_ai.configuration import selected_configuration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.adapters.pydantic_ai.scoping import get_model_scope
from nighthawk.core.calls import FunctionCall, get_function_call
from nighthawk.core.model_selection import ModelSelection
from nighthawk.core.scoping import get_scope
from nighthawk.core.step_context import StepContext
from nighthawk.core.step_request import StepCandidate, StepRequest


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


class RequestObserver(AbstractCapability[StepContext]):
    """Pydantic AI collapses absent usage to zeros, so an all-zero total counts as unknown."""

    def __init__(self, threshold: int = 100, host_unit: str = "test") -> None:
        super().__init__()
        self.threshold = threshold
        self.host_unit = host_unit
        self.observed_tokens = 0
        self.records: list[RequestObservation] = []
        self.lock = threading.Lock()

    # Retain the dependency's keyword-compatible parameter name on this override.
    async def wrap_model_request(
        self, ctx: RunContext[StepContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        call = get_function_call()
        with self.lock:
            record = RequestObservation(
                len(self.records) + 1,
                self.host_unit,
                call.call_id if call else None,
                get_scope().execution_reference.step_execution_id,
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
        self.calls: list[FunctionCall] = []
        self.outcomes: list[str] = []
        self.refuse = False

    def accept(self, call: FunctionCall) -> None:
        self.calls.append(call)
        if self.refuse:
            self.outcomes.append("refused")
            raise PermissionError("Host refused invocation")

    def invoke(self, call: FunctionCall, proceed: Callable[[], object]) -> object:
        self.accept(call)
        try:
            result = proceed()
        except BaseException:
            self.outcomes.append("failed")
            raise
        self.outcomes.append("returned")
        return result

    async def invoke_async(self, call: FunctionCall, proceed: Callable[[], Awaitable[object]]) -> object:
        self.accept(call)
        try:
            result = await proceed()
        except BaseException:
            self.outcomes.append("failed")
            raise
        self.outcomes.append("returned")
        return result


class PreservingExecutor:
    def __init__(self, executor: AgentStepExecutor, model_name_to_model: dict[str, FunctionModel], observer: RequestObserver) -> None:
        self.executor = executor
        self.model_name_to_model = model_name_to_model
        self.observer = observer

    def resolve_model_selection(self, selection: ModelSelection) -> PreservingExecutor:
        configuration = selected_configuration(selection, self.executor.configuration)
        model = configuration.execution_model
        if isinstance(model, str):
            if model not in self.model_name_to_model:
                raise PermissionError("Model selection is not allowed")
            configuration = configuration.model_copy(update={"execution_model": self.model_name_to_model[model]})
        elif not any(model is allowed for allowed in self.model_name_to_model.values()):
            raise PermissionError("Borrowed model is not allowed")
        return PreservingExecutor(AgentStepExecutor(configuration=configuration), self.model_name_to_model, self.observer)

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        assert self.observer in get_model_scope().capabilities
        return self.executor.run_step(request=request)

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        assert self.observer in get_model_scope().capabilities
        return await self.executor.run_step_async(request=request)


def deterministic_model(name: str, tokens: int = 60) -> FunctionModel:
    async def respond(messages: list[ModelMessage], information: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}})],
            model_name=name,
            usage=RequestUsage(input_tokens=tokens - 10, output_tokens=10),
        )

    return FunctionModel(respond, model_name=name)


@nh.function
def block() -> None:
    """execute
    Complete this block without changing anything.
    """


@nh.function
async def child() -> None:
    with nh.scope(execution_model="allowed:B"):
        """execute
        Complete this child block without changing anything.
        """
