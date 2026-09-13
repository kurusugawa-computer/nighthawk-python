# Runtime configuration

> This page assumes you have completed [Executors](executors.md).

This page covers scope composition, model selection, function admission, request observation, cleanup, and execution identity, alongside prompt and context settings.
Scope composition is shared across backends; model-selection support and request visibility depend on the active executor.

## Scoped overrides with `nh.scope()`

Use `nh.scope()` to override execution settings within an existing run. Each scope generates a new `scope_id` while keeping the current `run_id`.

```py
with nh.run(step_executor):
    # Replace the full executor configuration, including defaults for its omitted fields.
    # Other omitted scope arguments inherit the parent scope values.
    with nh.scope(
        step_executor_configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.6-luna",
        ),
    ) as scoped_executor:
        expensive_analysis(data)

    # Replace explicitly provided values. [] / {} clear collections.
    with nh.scope(
        system_prompt_suffix_fragments=["Always respond in formal English."],
        task_references={},
    ):
        formal_summary(text)

    # Replace the step executor entirely for a section
    with nh.scope(step_executor=another_executor):
        specialized_step(data)

    # Inject task helpers for this scope
    with nh.scope(task_references={"search_repository": search_repository}):
        typed_labeling_step(ticket_text)

    # Add operation and commit oversight for this scope
    with nh.scope(
        oversight=nh.oversight.Oversight(
            inspect_operation=inspect_operation,
            inspect_step_commit=inspect_step_commit,
        )
    ):
        inspected_step(ticket_text)
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

`StepExecutorConfiguration` also accepts `system_prompt_suffix_fragments` and `user_prompt_suffix_fragments` (tuples of strings) as baseline suffix fragments for the whole run. Resolved scope fragments follow configuration-level baseline fragments. Clearing scope fragments preserves that configuration baseline.

System prompt suffix fragments are rendered with the same `$tool_result_max_tokens` placeholder used by built-in prompt templates. Use `$$tool_result_max_tokens` if the literal text must appear in the final prompt.

Text-projected backends that expose Nighthawk tools automatically add a short tool-result preview warning to the system prompt. You do not need to add a separate preview warning fragment for coding-agent or other text-projected tool transports.

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

Mappings replace inherited references. Use `nh.Merge({"child": search_repository})` to add references with identity-based conflict checks.

```py
with nh.run(step_executor), nh.scope(task_references={"parent": search_repository}):
    with nh.scope(task_references={}):
        triage_issue(ticket_text)
```

The inner scope above clears inherited task references.

```py
with nh.scope(task_references={"child": search_repository}):
    triage_issue(ticket_text)
```

This fully replaces inherited references with the provided mapping.

```py
with nh.scope(task_references=nh.UNSET):
    triage_issue(ticket_text)
```

This keeps inherited references unchanged.

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

Use list values when setting scope-level suffix fragments:

```py
with nh.run(step_executor):
    with nh.scope(system_prompt_suffix_fragments=["Use concise answers."]):
        summarize_ticket(ticket_text)
```

`user_prompt_suffix_fragments` follows the same rules.

```py
with nh.scope(user_prompt_suffix_fragments=["Focus on actionable output."]):
    summarize_ticket(ticket_text)
```

Use `nh.Extend(["child"])` to append fragments; plain sequences replace inherited scope fragments.

```py
with nh.run(step_executor), nh.scope(system_prompt_suffix_fragments=["parent"]):
    with nh.scope(system_prompt_suffix_fragments=["child_1", "child_2"]):
        summarize_ticket(ticket_text)
```

Pass `[]` to clear inherited fragments.

```py
with nh.scope(system_prompt_suffix_fragments=[]):
    summarize_ticket(ticket_text)
```

Omit the field or pass `nh.UNSET` to keep inherited fragments unchanged.

```py
with nh.scope(system_prompt_suffix_fragments=nh.UNSET):
    summarize_ticket(ticket_text)
```

The same `replace` semantics apply to `user_prompt_suffix_fragments`.

## Reading the active scope

Within an active `nh.run()` context, snapshot getters expose the current scope's accumulated state:

- `nh.get_system_prompt_suffix_fragments()` returns the system prompt suffix fragments as a `tuple[str, ...]`.
- `nh.get_user_prompt_suffix_fragments()` returns the user prompt suffix fragments as a `tuple[str, ...]`.
- `nh.get_tools()` returns the scoped native tools as a `tuple[Tool[StepContext], ...]`, excluding built-in tools.
- `nh.get_capabilities()` returns the scoped Pydantic AI capabilities as a tuple.
- `nh.get_lifecycle()` returns the active `StepLifecycle`, or `None`.
- `nh.get_oversight()` returns the active `Oversight`, or `None` when none is installed.

The prompt suffix getters return only fragments accumulated via `nh.scope(...)`; configuration-level baseline fragments from `StepExecutorConfiguration` are not included.

During a Natural step, task references are available through `nh.get_step_context().environment.task_name_to_value`.
When configuring nested scopes outside a step, keep the original task mapping and pass the desired subset as an ordinary mapping.

```py
task_name_to_value = {"a": helper_a, "b": helper_b}
with nh.run(step_executor):
    with nh.scope(task_references=task_name_to_value):
        selected_task_name_to_value = {name: value for name, value in task_name_to_value.items() if name != "b"}
        with nh.scope(task_references=selected_task_name_to_value):
            triage_issue(ticket_text)
```

All getters listed above, including `nh.get_oversight()`, require an active run context. Outside `nh.run()` they raise `NighthawkError`, matching `nh.get_step_executor()` semantics. Catch `NighthawkError` if a helper needs to detect the absence of a run.

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

This example is an in-memory history. Durable storage, serialization, and deduplication belong to the host. Callback failures do not roll back assignments or retry execution. See [terminal delivery semantics](specification.md#104-terminal-delivery-and-host-ledgers) for exception chaining, cancellation, and storage failure handling.

For a host ledger, use `FunctionCallHandler` for admission before Python body effects, `inspect_step_commit` for approval before assignment, and `StepLifecycle` for block terminal recording and exception translation.
The executor boundary covers its own block processing, after earlier Python code and block preparation may already have effects.
Correlate block records with `execution_reference.step_execution_id`, which is unique per block execution; `source_location` only groups executions of the same source block.
Use `nh.get_function_call().call_id` when a handler is active to associate blocks and request observations with their enclosing function invocation.
Preparation failures may have no executor admission record.

If an earlier host boundary already stored a failure and raised its public exception, the lifecycle handler still receives StepFailed. Skip a duplicate append only after verifying that exception refers to the stored event for this same execution, then rethrow the same exception. Core preserves its original cause. Merely returning from the handler results in an ExecutionError wrapper; suppressing all exceptions of the host type would incorrectly omit parent events for failed nested executions.

At a host-controlled admission boundary, `nh.get_lifecycle() is expected_lifecycle` can verify that the required callback is configured. If it has been cleared, record the bypass through that still-active boundary: the missing lifecycle cannot record its own absence. Delivery is one in-process attempt with a configured handler, not guaranteed persistence. A callback exception after successful assignment propagates without undoing writes; host delivery-failure and anomaly records are separate from the completed core record. The full rules are in [terminal delivery semantics](specification.md#104-terminal-delivery-and-host-ledgers).

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

Here `credentials` is supplied by the host. The host owns credential delivery, including Docker input channels, and the client lifetime. Nighthawk borrows the Model without extracting keys or transporting them through environment variables, process launch arguments, prompts, or its diagnostics. Pydantic AI may read environment variables; existing string-based provider authentication remains supported.

Managed scopes can replace the Model, including with another instance of the same name, and restore the exact parent executor. For prompt-only changes, `configuration.model_copy(update={"prompts": nh.StepPromptTemplates(step_system_prompt_template="Updated instructions")})` retains the Model. Supply valid typed updates: `model_copy` does not validate them. External agents own model selection: `from_agent` rejects instance-valued configuration models, and scopes reject changes to their configured model string.

Live Models are runtime resources, not portable settings. Configuration representations omit the model. Dumps containing it raise `PydanticSerializationError`; use `configuration.model_dump(exclude={"model"})` for partial settings. Explicit tokenizer encoding wins; otherwise the model name is used, with `o200k_base` as fallback. Configuration settings override Model defaults under Pydantic AI's usual precedence. Forwarding `openai_store=False` sets the request field; it does not certify remote retention behavior. The [specification](specification.md#52-configuration) owns this contract.

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

The request exposes the live environment, Natural program, explicit references, output contracts, and configured budgets.
Changing presentation does not change which values runtime operations can access.
Prompt suffix fragments are appended once after your builder returns.
Nested scopes inherit the builder unless replaced, and a new run uses the default.
The complete contract is in [context construction](specification.md#824-replaceable-context-construction).

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

### Whole-function admission

A host installs `nh.function_call_scope(handler)` independently of an executor run.
Its `invoke(call, proceed)` method calls `proceed()` synchronously; `invoke_async(call, proceed)` awaits `proceed()`.
Refuse by raising before continuation entry.
Returning without completing the continuation is a contract error.
The continuation may start once, only during its handler's active invocation and in the same Task and thread.
A handler can establish `nh.run(executor)` before proceeding; no run is required to refuse the call.
Previously created aliases still enter the handler.
An async function uses the context in which its coroutine executes, and a Task uses its own selected context.
`nh.function_call_scope(None)` clears the handler explicitly; nested `nh.run()` contexts do not clear it.
Completion includes Python `finally` processing and explicitly joined children; intercepted generators and async generators are unsupported.
A synchronous Python return of a coroutine preserves that exact object without awaiting or closing it; DSL tool expressions retain their separate auto-await behavior.

`nh.get_natural_function_definition(function)` returns the exact registered wrapper's original function, public wrapper, and transformed body, or `None` for an unrelated callable.
Standard method descriptors are normalized, but copied attributes and unrelated outer wrappers are not registration evidence.
The registry does not retain abandoned namespaces; a returned snapshot intentionally retains its callable objects while the host holds it.
Use `nh.get_transformed_function()` when conventional outer-wrapper traversal is desired instead.
Call identities and exact definitions are described in the [boundary contract](specification.md#embedded-execution-boundaries).
The executable [embedded execution example](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/embedded_execution.py) demonstrates both invocation modes without credentials.

### Request usage and admission

For request budgets, observe available usage in a Pydantic AI wrapping capability and refuse the next request when accumulated observations reach your threshold.
Two requests totaling 120 may be accepted against 100 before the next sequential request is refused; concurrent requests may overrun further.
Keep these host request records separate from the `UsageMeter` aggregate to avoid double counting.
Missing reported model names remain unknown; absent usage may already be normalized by the dependency and is not proof of zero cost.
Keep one host accounting record per visible request, correlated to the block and intercepted function, and record failure and cancellation separately from success.
Pydantic AI wrapping hooks can observe `BaseException`, including cancellation; ordinary request-error hooks handle `Exception` only.
SDK retries, HTTP attempts, and coding-agent internal requests may be hidden within one capability-visible request.
Backend-specific facilities are needed for stricter observation; Core does not infer missing requests or usage.

### Completion and interruption

`StepLifecycle.interruption_delivery_timeout` now requests cancellation of an interrupted notification rather than setting an overall completion deadline.
Core waits for started, operation-finished, and terminal notification cleanup, including after repeated caller cancellation.
The synchronous bridge delivers caller interruption cooperatively and joins its worker and owned loop shutdown before propagating it.
Existing cancellation cleanup is preserved, and the first interruption retains its identity with additional cleanup failures chained to it.
Async callers retain ownership of their event loops and unrelated tasks.
Resistant cleanup can block indefinitely; the host still owns detached application work and durable recording.
An executor return, a completed block, and function completion remain separate facts; failure never implies rollback or permission to replay.

## Next steps

Continue to **[Patterns](patterns.md)** for outcomes, error handling, async, cross-block composition, resilience, and common mistakes.
