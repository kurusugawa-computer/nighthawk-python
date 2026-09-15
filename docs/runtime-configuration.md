# Runtime configuration

> This page assumes you have completed [Executors](executors.md).

This page covers scope composition, model selection, function admission, request observation, cleanup, and execution identity, alongside prompt and context settings.
Scope composition is shared across backends; model-selection support and request visibility depend on the active executor.

## Scoped overrides with `nh.scope()`

Use `nh.scope()` to override execution settings within an existing run. Each scope generates a new `scope_id` while keeping the current `run_id`.

The examples below use a configured `step_executor` and application functions such as `summarize_ticket`.
For a complete initial setup, see [Quickstart](quickstart.md).

```py
with nh.run(step_executor):
    with nh.scope(user_prompt_suffix_fragments=["Use concise answers."]):
        summarize_ticket(ticket_text)
```

Parameters:

- `model`: replace only the model while preserving other executor configuration fields; cannot be combined with `step_executor_configuration`.
- `step_executor_configuration`: replace the entire configuration.
- `step_executor`: replace the step executor entirely.
- `usage_meter`: replace the `UsageMeter` that steps in this scope record into. See [Usage metering](#usage-metering).
- `oversight`: operation and resolved commit inspection, with direct or awaitable decisions.
- `lifecycle`: started, operation-finished, and terminal notifications, with direct or awaitable completion.
- `context_builder`: replace context presentation without changing value access or validation.
- `system_prompt_suffix_fragments`: scope-level system suffix fragments.
- `user_prompt_suffix_fragments`: scope-level user suffix fragments.
- `task_references`: values in the scoped task environment.
- `tools`: scope-level native tools. See [Scoped tools](#scoped-tools).
- `capabilities`: scope-level Pydantic AI capabilities. See [Pydantic AI capabilities in scopes](#pydantic-ai-capabilities-in-scopes).

Composition is selected by each supplied value:

- Omission or `nh.UNSET` inherits the parent value.
- Ordinary values replace; `[]` and `{}` clear their collections.
- `nh.Extend(sequence)` appends prompt fragments, tools, or capabilities.
- `nh.Merge(mapping)` merges task references, rejecting a same-name value unless it is the identical Python object.

`oversight` and `lifecycle` accept `None`, which clears their hooks. Other scope fields reject it. `usage_meter` replaces the enclosing meter without forwarding totals. Resolution errors leave the parent context intact.

The context manager yields the resolved `StepExecutor` for the scope.

## Scoped task references

`task_references` supplies helpers and values through a separate task environment.
It does not modify Python globals; ordinary Python names take precedence.
Use `environment.resolve(name, source="task")` when deliberately selecting a shadowed injected value.
For example:

```py
def search_repository(query: str) -> list[str]: ...


with nh.run(step_executor):
    with nh.scope(task_references={"search_repository": search_repository}):
        triage_issue(ticket_text)
```

Use the [composition rules](#scoped-overrides-with-nhscope) to replace, merge, or clear task references.

## Scoped tools

`tools` declares native tools for a scope: callables exposed through the model's native tool-calling interface. Nighthawk has no global tool registry; a tool is visible exactly where a `nh.scope(tools=[...])` makes it visible.

```py
from pydantic_ai import RunContext
from pydantic_ai.tools import Tool


def read_step_execution_id(run_context: RunContext[nh.StepContext]) -> str:
    """Return the current step Id."""
    return run_context.deps.execution_reference.step_execution_id or ""


with nh.run(step_executor):
    with nh.scope(tools=[read_step_execution_id, Tool(lookup_user, name="find_user")]):
        triage_issue(ticket_text)
```

Each element is either a plain callable or a Pydantic AI `Tool`. A plain callable is wrapped with `Tool(callable)`: the tool name is the function name, the description is the docstring, and a leading `RunContext[StepContext]` parameter is detected automatically. Pass a `Tool` instance to override the name, description, or metadata.

A plain sequence replaces tools; `tools=[]` clears scoped declarations. `nh.Extend([lookup])` appends tools. Repeating an identical callable or exact `Tool` object is idempotent and retains the first normalized tool and order. Different wrappers conflict, even when they wrap the same callable. `ToolNameConflictError` is both a `ToolDeclarationError` and a `NameConflictError`. Built-in names remain reserved.

`task_references` and `tools` have distinct roles. `task_references` injects Python names into the block's namespace: they render as signature lines and the model invokes them through Nighthawk's expression tool, at the cost of one line of prompt context. `tools` exposes callables through native tool calling: each tool adds a JSON Schema to every model request, passes through Nighthawk's tool boundary and `inspect_operation`, and is served to coding-agent backends over MCP. Prefer `task_references` for Python helpers. Reserve `tools` for cases that need native tool calling, such as strict argument schemas, Pydantic AI tool features (`ModelRetry`, `ApprovalRequired`, timeouts), or first-class tool exposure to a coding agent. Do not expose the same function through both.

## Pydantic AI capabilities in scopes

`capabilities` attaches Pydantic AI [capabilities](https://ai.pydantic.dev/capabilities/overview/) to every step executed in a scope. Nighthawk passes them to `Agent.run(capabilities=...)`, so they apply to managed agents and to agents supplied through `AgentStepExecutor.from_agent(...)` alike.

```py
from pydantic_ai.capabilities import Hooks, Instrumentation


async def log_model_request(context, request_context):
    print(f"model request with {len(request_context.messages)} messages")
    return request_context


with nh.run(step_executor):
    with nh.scope(capabilities=[Hooks(before_model_request=log_model_request), Instrumentation()]):
        triage_issue(ticket_text)
```

Use capabilities to observe, gate, or instrument model requests with Pydantic AI's own hook vocabulary (`before_model_request`, `after_model_request`, `wrap_model_request`, and the rest of `AbstractCapability`). Nighthawk's `oversight` covers tool calls, return-expression approval, and resolved step commits; the model request boundary belongs to Pydantic AI. Use `nh.Extend([capability])` to append capabilities; a plain list replaces them. Repeated capability entries are preserved.

## Prompt suffix fragments in scopes

Set system and user suffixes together; use `nh.Extend` to append to inherited fragments:

```py
with nh.run(step_executor), nh.scope(system_prompt_suffix_fragments=["Be concise."]):
    with nh.scope(
        system_prompt_suffix_fragments=nh.Extend(["Cite sources."]),
        user_prompt_suffix_fragments=["Focus on actionable output."],
    ):
        summarize_ticket(ticket_text)
```

Configuration-level suffixes precede scope fragments.
Clearing scope fragments with `[]` preserves that configuration baseline.
System suffixes expand `$tool_result_max_tokens`; use `$$` for a literal dollar sign.

## Reading the active scope

Use `nh.get_step_executor()`, `nh.get_lifecycle()`, or `nh.get_capabilities()` to inspect the selected configuration.
Scope getters require an active run and raise `NighthawkError` outside it.
`nh.get_step_context()` additionally requires an active step and exposes `environment.task_name_to_value` for task references.

Prompt suffix getters return scope fragments without configuration-level baselines.
See [API reference](api.md) for the accessor inventory and return types.

## Record completed executions

Install `nh.lifecycle.StepLifecycle` to record Natural block endings, including preparation, validation, and return-await failures.
Successful block notification follows generated assignments; the containing Python function may still run more code or fail in its `finally` block.
Use a [function-call handler](#whole-function-admission) to observe that eventual function outcome.
The lifecycle callback can raise a host exception referring to its stored record.
Use `Oversight.inspect_operation` to inspect expression execution and `inspect_step_commit` to inspect validated results.
All hooks support direct or asynchronous completion.

```python
import nighthawk as nh

execution_id_to_event: dict[str, nh.lifecycle.StepFinished] = {}


def record_finished(event: nh.lifecycle.StepFinished) -> None:
    execution_id = event.execution_reference.step_execution_id
    assert execution_id is not None
    execution_id_to_event[execution_id] = event


# Inside an active nh.run(executor):
with nh.scope(lifecycle=nh.lifecycle.StepLifecycle(record_finished)):
    result = my_natural_function()
```

This example is an in-memory history. Durable storage, serialization, and deduplication belong to the host. Callback failures do not roll back assignments or retry execution.

Choose the observation boundary by purpose: function admission before Python body effects, commit inspection before assignment, or lifecycle notification at block exit.
Use `step_execution_id` to correlate block records and the active `FunctionCall.call_id` to group them by function invocation.
The [delivery contract](specification.md#104-terminal-delivery-and-host-ledgers) defines exception translation and deduplication responsibilities.

## Oversight in scopes

Use `nh.scope(oversight=...)` to inspect operations before execution and validated results before Python assignment.
Each callback may return its decision directly or asynchronously.

```py
import nighthawk as nh
from nighthawk.runtime import OperationRequest, Return


def inspect_operation(operation: OperationRequest) -> nh.oversight.Accept | nh.oversight.Reject:
    if operation.kind == "native_tool" and operation.tool_name == "delete_file":
        return nh.oversight.Reject("Deletion requires host approval.")
    return nh.oversight.Accept()


def inspect_step_commit(commit: nh.oversight.StepCommit) -> nh.oversight.StepCommitDecision:
    if isinstance(commit.outcome, Return) and commit.outcome.value is None:
        return nh.oversight.Reject("A result is required.")
    return nh.oversight.Accept()


oversight = nh.oversight.Oversight(
    inspect_operation=inspect_operation,
    inspect_step_commit=inspect_step_commit,
)
```

Install the configuration with `nh.scope(oversight=oversight)` inside an active run.
Operation inspection precedes execution and cannot undo earlier side effects.
Rejection terminates the step; it is not a model retry.
Commit inspection receives resolved `Pass`, `Return`, `Break`, `Continue`, or `Raise` outcomes and validated output values.
`Raise.error_type` is an exception class; its constructor runs only after inspection.

Use `Rewrite(outcome=Return(value))` to replace a return, including an explicit `None` when the annotation permits it.
Use `Rewrite(binding_name_to_value={...})` to replace the complete output mapping.
Omitted fields inherit the original validated values through `nh.UNSET`.
Acceptance does not repeat validators, and rewriting does not repeat inspection or expression evaluation.
A bindings-only rewrite retains the previously resolved return value.

Inspection mappings retain Python types and identity in shallow read-only views.
Use `Rewrite` for changes and explicit copying or serialization for durable history.
The complete contracts are in [commit semantics](specification.md#102-host-commit-boundary) and [operation inspection](specification.md#103-operation-inspection).

## Caller-authenticated models

A host can receive credentials in memory and construct a Pydantic AI Model before handing it to the ordinary managed executor:

```py
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIResponsesModel("gpt-5.6-luna", provider=OpenAIProvider(api_key=credentials.api_key))
configuration = nh.StepExecutorConfiguration(
    model=model,
    model_settings={"openai_store": False},
    tokenizer_encoding="o200k_base",
)
executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
with nh.run(executor):
    result = workflow()
```

The host supplies `credentials` and owns the Model's client lifetime.
`nh.scope(model=another_model)` selects a borrowed Model while retaining the other configuration fields, then restores the parent executor on exit.
External agents created through `from_agent` own their model selection.

Live Models cannot be serialized as settings; use `configuration.model_dump(exclude={"model"})` for a partial export.
See the [configuration contract](specification.md#52-configuration) for ownership, serialization, and setting precedence.

## Mixing executors

Use `nh.scope(step_executor=...)` to switch executors within a single run. This is the standard pattern for mixing a cheap classifier with a deep autonomous step:

```py
fast_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna"),
)

deep_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="codex:default"),
)

with nh.run(fast_executor):
    label = classify_ticket(text)  # fast, cheap
    with nh.scope(step_executor=deep_executor):
        diagnosis = inspect_repository(text)  # deep, autonomous
```

See [Executors](executors.md#decision-tree) for when to choose a coding agent backend over a provider-backed executor.

## Custom context construction

Use `nh.scope(context_builder=builder)` when the application needs to choose or summarize the context presented to the model.
Implement `build(request: ContextRequest)` and return a sequence of Pydantic AI `UserContent` items.
Import `ContextRequest` and `DefaultContextBuilder` from `nighthawk.runtime`; the default builder can be called from your implementation when retaining part of its presentation.

The request exposes the live environment, Natural program, output contracts, and presentation budgets.
Presentation changes do not change runtime value access.
See [context construction](specification.md#824-replaceable-context-construction) for the contract.

## Context limits

The LOCALS and GLOBALS sections are bounded by token and item limits configured via `StepContextLimits`. When a limit is reached, remaining entries are omitted and a `<snipped>` marker is appended. The underlying data remains in Python memory and is accessible through binding functions at runtime -- truncation affects prompt coherence, not data availability.

```py
configuration = nh.StepExecutorConfiguration(
    model="openai-responses:gpt-5.6-luna",
    context_limits=nh.StepContextLimits(
        locals_max_tokens=4096,
        locals_max_items=50,
    ),
)
```

See [Specification Section 8.2](specification.md#82-prompt-context) for the full specification.

## JSON rendering style

`StepExecutorConfiguration` also accepts `json_renderer_style`, which controls how values are rendered in prompt context and tool results (e.g., strict JSON vs annotated pseudo-JSON with omission markers). See [Specification Section 5.2](specification.md#52-configuration) for available styles.

## Runtime execution identity

Each `nh.run()` generates an `ExecutionReference` with a unique `run_id` (trace root) and `scope_id`. Nested `nh.scope()` calls generate new `scope_id` values while keeping the same `run_id`.

```py
execution_reference = nh.get_execution_reference()
execution_reference.run_id  # trace root -- stable across nested scopes
execution_reference.scope_id  # current scope -- changes with each nh.scope()
execution_reference.step_execution_id  # Unique per block execution; None outside a step
execution_reference.parent_step_execution_id  # Active parent step, when present
execution_reference.source_location  # Source module and line; None outside a step
```

Use `run_id` to correlate distributed agent processes in logs and traces. Use `scope_id` to identify the current logical execution context. See [Specification Section 10](specification.md#10-runtime-scoping) for the full specification and [Verification: observability](verification.md#observability) for tracing integration.

An intercepted function has its own `FunctionCall.call_id` and `parent_call_id`, independent of block and run identities.
`nh.get_function_call()` returns the current intercepted call or `None`, including outside a run; function admission can precede run creation.

## Usage metering

Each `nh.run()` creates a thread-safe `UsageMeter`.
`AgentStepExecutor` records available aggregate usage after its agent run returns without a recorded operation failure, before candidate resolution, Python writeback, and block notification finish.
An agent run that fails or is cancelled before that point may have consumed tokens without adding them to the meter; a metered run can still fail later validation or delivery.
Custom executors own their metering integration.
Pass `nh.run(step_executor, usage_meter=meter)` to supply your own meter, and `nh.scope(usage_meter=meter)` to meter a nested section in isolation: steps inside the scope record into the scoped meter only, and totals are not forwarded to the enclosing meter.

```py
with nh.run(step_executor):
    meter = nh.get_usage_meter()
    meter.total_tokens  # cumulative tokens
    meter.snapshot()  # independent RunUsage copy of current totals
```

`get_usage_meter()` requires an active `nh.run()` context.
Use it for aggregate decisions between calls; [Patterns: Budget](patterns.md#budget) describes the function-level budget transformer.
For admission between requests within an unfinished block, use [request usage and admission](#request-usage-and-admission).

## Model selection and function admission

`model=` changes only the model; `step_executor_configuration=` replaces the whole configuration.
Both accept omission or `nh.UNSET` for inheritance and cannot be supplied together.
The active executor resolves selection through `ConfigurableStepExecutor`; a host wrapper must return a wrapper retaining its controls.
Unsupported executors fail before model requests, and custom `AgentStepExecutor` subclasses must explicitly implement resolution.

```python
@nh.natural_function(model="openai-responses:gpt-5.6-luna")
async def child() -> int:
    """natural
    Return the integer 17.
    """
    return 0


with nh.run(executor), nh.scope(capabilities=[request_observer]):
    with nh.scope(model=borrowed_model):
        result = parent()
```

Model declarations are inert until invocation is accepted; they neither initialize providers during decoration nor require credentials for definition lookup.
Selection is applied before the Python body, and parent settings are restored before the host call handler observes return or failure.
The same meter, capabilities, oversight, lifecycle, task references, and call handler survive model-only selection.
Explicit replacement, clearing hooks, or starting another run remains application-controlled.

### Preserve host model policy

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
        model = configuration.model
        if isinstance(model, str):
            if model not in self.model_name_to_model:
                raise PermissionError("Model is not allowed")
            configuration = configuration.model_copy(update={"model": self.model_name_to_model[model]})
        elif not any(model is allowed for allowed in self.model_name_to_model.values()):
            raise PermissionError("Borrowed model is not allowed")
        executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
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


@nh.natural_function
def work() -> None:
    """natural
    Complete this simulated task.
    """


@nh.natural_function(model="allowed:B")
async def child() -> None:
    """natural
    Complete this simulated child task.
    """


model_name_to_model = {"allowed:A": simulated_model("A"), "allowed:B": simulated_model("B")}
executor = AllowedModels(
    nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model=model_name_to_model["allowed:A"])),
    model_name_to_model,
)
with nh.run(executor):
    work()
    asyncio.run(child())
    assert nh.get_step_executor() is executor
    work()
    try:
        with nh.scope(model="unapproved:model"):
            raise AssertionError("Unapproved model should have been refused")
    except PermissionError:
        pass
    assert nh.get_step_executor() is executor

assert requested_models == ["A", "B", "A"]
print("models=A,B,A; unapproved_model_refused=True")
```
<!-- /runnable-example:model_selection -->

### Whole-function admission

A host installs `nh.function_call_scope(handler)` independently of an executor run.
Its `invoke(call, proceed)` method calls `proceed()` synchronously; `invoke_async(call, proceed)` awaits `proceed()`.
Refuse by raising before continuation entry.
Returning without completing the continuation is a contract error.
The continuation may start once, only during its handler's active invocation and in the same Task and thread.
A handler can establish `nh.run(executor)` before proceeding; no run is required to refuse the call.
Function completion includes Python `finally` processing and explicitly joined children.
Generators are unsupported at this boundary.
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


@nh.natural_function(model="unconfigured:model")
def refused_work() -> None:
    body_effects.append("refused body")


@nh.natural_function
def summarize(text: str) -> str:
    summary: str = ""
    """natural
    Summarize <text> in <:summary>.
    """
    return summary


@nh.natural_function
async def summarize_async(text: str) -> str:
    summary: str = ""
    """natural
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
    executor = ScriptedExecutor(responses=[pass_response(summary="First"), pass_response(summary="Second")])
    with nh.run(executor):
        assert summarize("First document") == "First"
        assert asyncio.run(summarize_async("Second document")) == "Second"

assert len(set(admission.completed_calls)) == 2
print("refused_body_effects=0; completed_calls=2")
```
<!-- /runnable-example:function_admission -->

### Request usage and admission

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
        parts=[ToolCallPart("nh_eval", {"expression": "1"}, str(request_count))],
        usage=RequestUsage(input_tokens=50, output_tokens=10),
    )


@nh.natural_function
def investigate() -> None:
    """natural
    Investigate until the task is complete.
    """


budget = RequestBudget(threshold=100)
executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model=FunctionModel(simulated_response)),
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

### Completion and interruption

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


@nh.natural_function
async def work() -> None:
    """natural
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

## Next steps

Continue to **[Patterns](patterns.md)** for outcomes, error handling, async, cross-block composition, resilience, and common mistakes.
