# Host integration

> This page assumes you have completed [Runtime configuration](runtime-configuration.md).

Use these connection points when an application needs to admit functions, restrict model selection, account for requests, or record completion.
Ordinary Natural functions remain reusable: the host supplies these policies around their Python calls.
Each boundary has its own meaning; a completed model request does not establish that a block or its containing function succeeded.

| Host responsibility | Connection point |
|---|---|
| Accept or refuse a function before its body runs | `function_call_scope` and `FunctionCallHandler` |
| Preserve model-selection policy across scopes | `ConfigurableStepExecutor.resolve_model_selection` |
| Observe and admit visible model requests | Pydantic AI request capabilities |
| Inspect individual operations and pending writes | [Scoped oversight](runtime-configuration.md#oversight-in-scopes) |
| Record block completion and interruption | [Lifecycle notifications](runtime-configuration.md#record-completed-executions) |

The [specification](specification.md#embedded-execution-boundaries) defines the guarantees and their limits.
Applications own durable storage, acceptance policies, and recovery from partial effects.
For hosts that provide an explicit namespace instead of a decorated function, see [Embedded execution](embedded-execution.md).

## Preserve host model policy

A host wrapper must return another wrapper when resolving a model selection.
Returning the underlying executor would discard the host's policy for subsequent selections.
This complete example permits two model objects and exercises both synchronous and asynchronous execution.
`FunctionModel` simulates responses without credentials; it verifies routing rather than judgment quality.
Save the block as `model_selection.py` and run it with Python after installing Nighthawk.

<!-- runnable-example:model_selection -->
```py
from __future__ import annotations

import asyncio

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import nighthawk as nh
from nighthawk.runtime import StepCandidate, StepRequest


class AllowedModels:
    def __init__(self, executor: nh.AgentStepExecutor, model_name_to_model: dict[str, FunctionModel]) -> None:
        self.executor = executor
        self.model_name_to_model = model_name_to_model

    def resolve_model_selection(self, selection: nh.ModelSelection) -> AllowedModels:
        configuration = selection.apply(self.executor.configuration)
        model = configuration.execution_model
        if isinstance(model, str):
            if model not in self.model_name_to_model:
                raise PermissionError("Model is not allowed")
            configuration = configuration.model_copy(update={"execution_model": self.model_name_to_model[model]})
        elif not any(model is allowed for allowed in self.model_name_to_model.values()):
            raise PermissionError("Borrowed model is not allowed")
        executor = nh.AgentStepExecutor(configuration=configuration)
        return AllowedModels(executor, self.model_name_to_model)

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        return self.executor.run_step(request=request)

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        return await self.executor.run_step_async(request=request)


requested_models: list[str] = []


def simulated_model(name: str) -> FunctionModel:
    async def respond(messages: list[ModelMessage], information: AgentInfo) -> ModelResponse:
        requested_models.append(name)
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}})])

    return FunctionModel(respond, model_name=name)


@nh.function
def work() -> None:
    """execute
    Complete this simulated task.
    """


@nh.function
async def child() -> None:
    with nh.scope(execution_model="allowed:B"):
        """execute
        Complete this simulated child task.
        """


model_name_to_model = {"allowed:A": simulated_model("A"), "allowed:B": simulated_model("B")}
executor = AllowedModels(
    nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=model_name_to_model["allowed:A"])),
    model_name_to_model,
)
with nh.run(executor):
    work()
    asyncio.run(child())
    assert nh.get_step_executor() is executor
    work()
    try:
        with nh.scope(execution_model="unapproved:model"):
            raise AssertionError("Unapproved model should have been refused")
    except PermissionError:
        pass
    assert nh.get_step_executor() is executor

assert requested_models == ["A", "B", "A"]
print("models=A,B,A; unapproved_model_refused=True")
```
<!-- /runnable-example:model_selection -->

## Whole-function admission

A host installs `nh.function_call_scope(handler)` independently of an executor run.
Omission or `nh.UNSET` inherits the enclosing handler, `None` disables interception, and a handler replaces it.
Handlers must provide callable `invoke` and `invoke_async` methods; invalid inputs fail before installing the scope.
Its `invoke(call, proceed)` method calls `proceed()` synchronously; `invoke_async(call, proceed)` awaits `proceed()`.
Refuse by raising before continuation entry.
Returning without completing the continuation is a contract error.
The continuation may start once, only during its handler's active invocation and in the same Task and thread.
A handler can establish `nh.run(executor)` before proceeding; no run is required to refuse the call.
Function completion includes Python `finally` processing and explicitly joined children.
Generators are unsupported at this boundary.
Direct `execute_step` and `execute_step_async` calls do not invoke this handler; hosts must authorize them at their own [embedded execution entry point](embedded-execution.md#authorize-the-execution).
For context timing, exact definition lookup, and coroutine ownership, see [execution boundaries](specification.md#embedded-execution-boundaries).

This complete example refuses an invocation before body effects or model resolution, then accepts synchronous and asynchronous judgments.
The host records a successful function completion only after the continuation returns.
`ScriptedExecutor` supplies fixed summaries so the example needs no provider credentials.
Save the block as `function_admission.py` and run it with Python.

<!-- runnable-example:function_admission -->
```py
import asyncio
from collections.abc import Awaitable, Callable

import nighthawk as nh
from nighthawk.testing import ScriptedExecutor, pass_response


class Admission:
    def __init__(self) -> None:
        self.allowed = False
        self.completed_calls: list[str] = []

    def check(self) -> None:
        if not self.allowed:
            raise PermissionError("Host refused invocation")

    def invoke(self, call: nh.FunctionCall, proceed: Callable[[], object]) -> object:
        self.check()
        result = proceed()
        self.completed_calls.append(call.call_id)
        return result

    async def invoke_async(self, call: nh.FunctionCall, proceed: Callable[[], Awaitable[object]]) -> object:
        self.check()
        result = await proceed()
        self.completed_calls.append(call.call_id)
        return result


body_effects: list[str] = []


@nh.function
def refused_work() -> None:
    with nh.scope(execution_model="unconfigured:model"):
        body_effects.append("refused body")


@nh.function
def summarize(text: str) -> str:
    summary: str = ""
    """execute
    Summarize <text> in <:summary>.
    """
    return summary


@nh.function
async def summarize_async(text: str) -> str:
    summary: str = ""
    """execute
    Summarize <text> in <:summary>.
    """
    return summary


admission = Admission()
with nh.function_call_scope(admission):
    try:
        refused_work()  # Refusal needs no run and precedes model resolution.
    except PermissionError:
        pass
    else:
        raise AssertionError("Invocation should have been refused")
    assert body_effects == []
    assert admission.completed_calls == []

    admission.allowed = True
    executor = ScriptedExecutor(
        responses=[pass_response(binding_name_to_value={"summary": "First"}), pass_response(binding_name_to_value={"summary": "Second"})]
    )
    with nh.run(executor):
        assert summarize("First document") == "First"
        assert asyncio.run(summarize_async("Second document")) == "Second"

assert len(set(admission.completed_calls)) == 2
print("refused_body_effects=0; completed_calls=2")
```
<!-- /runnable-example:function_admission -->

## Request usage and admission

For request budgets, observe available usage in a Pydantic AI wrapping capability and refuse the next request when accumulated observations reach your threshold.
Two requests totaling 120 may be accepted against 100 before the next sequential request is refused; concurrent requests may overrun further.
Keep these host request records separate from the `UsageMeter` aggregate to avoid double counting.
Missing reported model names remain unknown; absent usage may already be normalized by the dependency and is not proof of zero cost.
Keep one host accounting record per visible request, correlated to the block and intercepted function, and record failure and cancellation separately from success.
Pydantic AI wrapping hooks can observe `BaseException`, including cancellation; ordinary request-error hooks handle `Exception` only.
SDK retries, HTTP attempts, and coding-agent internal requests may be hidden within one capability-visible request.
Backend-specific facilities are needed for stricter observation; Core does not infer missing requests or usage.

The following complete example focuses on admission within one unfinished block.
Its host accumulator counts only usage reported by completed requests; it does not estimate unknown usage or maintain a durable request ledger.
Failures can consume unreported tokens, and a lock around the check and accounting does not reserve tokens for concurrent requests.
For durable records, apply the correlation and outcome rules above in a host-owned capability.

`FunctionModel` simulates repeated tool requests at 60 tokens each.
The host admits two requests, observes 120 tokens, and refuses the third even though the containing block has not finished.
Save this block as `request_budget.py` and run it with Python; no credentials are required.

<!-- runnable-example:request_budget -->
```py
import threading

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.capabilities.abstract import WrapModelRequestHandler
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

import nighthawk as nh


class RequestBudgetExceededError(RuntimeError):
    pass


class RequestBudget(AbstractCapability[nh.StepContext]):
    def __init__(self, threshold: int) -> None:
        super().__init__()
        self.threshold = threshold
        self.observed_tokens = 0
        self.lock = threading.Lock()

    # Keep the dependency's keyword-compatible parameter name.
    async def wrap_model_request(
        self, ctx: RunContext[nh.StepContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        with self.lock:
            if self.observed_tokens >= self.threshold:
                raise RequestBudgetExceededError("Observed request budget reached")
        response = await handler(request_context)
        with self.lock:
            self.observed_tokens += response.usage.total_tokens
        return response


request_count = 0


async def simulated_response(messages: list[ModelMessage], information: AgentInfo) -> ModelResponse:
    global request_count
    request_count += 1
    # Ask for another tool operation so this block needs another model request.
    return ModelResponse(
        parts=[ToolCallPart("nh_evaluate", {"expression": "1"}, str(request_count))],
        usage=RequestUsage(input_tokens=50, output_tokens=10),
    )


@nh.function
def investigate() -> None:
    """execute
    Investigate until the task is complete.
    """


budget = RequestBudget(threshold=100)
executor = nh.AgentStepExecutor(
    configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(simulated_response)),
)
with nh.run(executor), nh.scope(capabilities=[budget]):
    try:
        investigate()
    except nh.ExecutionError as exception:
        assert isinstance(exception.__cause__, RequestBudgetExceededError)
    else:
        raise AssertionError("Third request should have been refused")
    # The unfinished agent run never returned an aggregate to this meter.
    assert nh.get_usage_meter().total_tokens == 0

assert request_count == 2
assert budget.observed_tokens == 120
print("observed_tokens=120; next_request_refused=True; aggregate_tokens=0")
```
<!-- /runnable-example:request_budget -->

## Completion and interruption

`StepLifecycle.interruption_delivery_timeout` requests notification cancellation.
Core joins owned notification and synchronous-bridge cleanup before returning or propagating interruption; resistant cleanup can wait indefinitely.
The host owns detached work, event loops supplied to async calls, and durable records.
See [interruption and delivery](specification.md#104-terminal-delivery-and-host-ledgers) for exception ordering and cleanup guarantees.

This complete example holds notification cleanup open until the host explicitly releases it.
The cancelled function remains unfinished while that cleanup is pending.
Save the block as `notification_cleanup.py` and run it with Python; its executor uses a fixed response without credentials.
Automated checks should run the file in a subprocess with an independent timeout so a cleanup regression cannot hang the test runner.

<!-- runnable-example:notification_cleanup -->
```py
import asyncio
from contextlib import suppress

import nighthawk as nh
from nighthawk.lifecycle import StepFinished, StepLifecycle
from nighthawk.testing import ScriptedExecutor, pass_response


@nh.function
async def work() -> None:
    """execute
    Complete this simulated task.
    """


async def demonstrate_cleanup() -> None:
    entered = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    completed: list[str] = []

    async def record_finished(event: StepFinished) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            completed.append("notification")

    executor = ScriptedExecutor(responses=[pass_response()])
    with nh.run(executor), nh.scope(lifecycle=StepLifecycle(on_step_finished=record_finished)):
        task = asyncio.create_task(work())
        try:
            await entered.wait()
            task.cancel()
            await cleanup_started.wait()
            assert not task.done()
        finally:
            release_cleanup.set()
            with suppress(asyncio.CancelledError):
                await task
    assert completed == ["notification"]


asyncio.run(demonstrate_cleanup())
print("returned_after_notification_cleanup=True")
```
<!-- /runnable-example:notification_cleanup -->

