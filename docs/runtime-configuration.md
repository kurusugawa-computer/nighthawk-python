# Runtime configuration

> This page assumes you have completed [Executors](executors.md).

This page covers runs, scoped model selection, shared values, prompt settings, execution identity, and usage metering.
See [Host integration](host-integration.md) for function admission, request accounting, model policy, and completion boundaries.
Scope composition is shared across backends; model-selection support and request visibility depend on the active executor.

Select models directly for ordinary use:

```python
with nh.run(execution_model="openai-responses:gpt-6-luna", decision_model="openai-decisions:gpt-6-luna"):
    result = workflow(document)
```

Both fields default to `None`; only a block using an unconfigured model fails.
A nested scope can change either field independently.
See [Typed decisions](decisions.md) for input, result, and failure contracts.

## Omission, explicit values, and composition

`nh.UNSET` represents an omitted argument wherever the signature accepts it.
The receiving API defines the default behavior: scopes inherit, runs create fresh resources, and rewrites retain their current field.
Ordinary values select the complete value, including empty dictionaries and sequences.
`None` is accepted only where the parameter has a meaningful absent or disabled value.
Composition uses `nh.Merge(...)` or `nh.Extend(...)` in their supported scope fields.

| Argument | Omitted or `UNSET` | Explicit empty value | `None` |
|---|---|---|---|
| `scope(task_references=...)` | Inherit | `{}` removes scoped references | Invalid |
| `scope(tools=...)` and other sequence fields | Inherit | `[]` empties the scoped collection | Invalid |
| `scope(oversight=...)` or `scope(lifecycle=...)` | Inherit | Not a collection | Disable hooks |
| `function_call_scope(handler)` | Inherit | Not a collection | Disable interception |
| `run(..., run_id=...)` | Generate a fresh Id | `""` is invalid | Invalid |
| `run(..., usage_meter=...)` | Create a fresh meter | Not a collection | Invalid |
| Optional executor `configuration` | Create a fresh default configuration | Raw dictionaries are invalid | Invalid |

Configuration value objects resolve their own field defaults and do not retain `UNSET` as unresolved configuration.
`StepExecutorConfiguration.execution_model_settings` defaults to `{}` and rejects `None` and `UNSET`.
An empty settings mapping adds no overrides; model-owned defaults still apply.
Use `StepExecutorConfiguration.model_validate(data)` to parse raw configuration data before supplying it to an executor.
Mappings are shallow snapshots: their values retain identity, and `{"a": None}` supplies a value rather than deleting a key.
Invalid run identifiers and usage meters fail before model resolution, leaving an enclosing run intact.

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

- `execution_model` and `decision_model`: independently replace each model; `None` clears a selection. These cannot be combined with `step_executor_configuration`.
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

`oversight` and `lifecycle` accept `None`, which clears their hooks. The two model fields accept `None` to clear their selections. Other scope fields reject it. `usage_meter` replaces the enclosing meter without forwarding totals. Invalid argument shapes fail before model resolution; resolution errors leave the parent context intact.

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

### Selecting shared values with Python

Natural calls do not implicitly copy parent arguments, locals, closure values, or step temporaries into the child.
Use parameters for essential inputs and `task_references` for references intentionally shared throughout a scope.
A Python helper can select names from `nh.get_step_context().environment` and return an ordinary dictionary.
The helper runs where it is called; Core does not rerun it during later step preparation.

Omitting `task_references` keeps the enclosing references; supplying a dictionary replaces them, `{}` clears them, and `nh.Merge(...)` adds references with identity-based conflict checking.
Replacement or clearing affects newly prepared steps, not an already active parent's environment.
Children and grandchildren see the selected references until another scope replaces them; their own new working values are not automatically shared.
An explicit dictionary and a dictionary returned by a helper have identical sharing semantics.

The synchronous example starts with an explicit embedded namespace, selects one policy, and calls an existing Natural function.
Neither the parent's conversation input nor its temporary becomes a child binding.
The example uses a deterministic executor and needs no model credentials.

<!-- runnable-example:shared_values_sync -->
```py
import nighthawk as nh
from nighthawk.runtime import StepDefinition, StepEnvironment, execute_step
from nighthawk.testing import CallbackExecutor, StepCall, StepResponse, pass_response


@nh.function
def child(value: int) -> None:
    """execute
    Inspect <value> using <policy>.
    """


def select_shared_references(environment: StepEnvironment) -> dict[str, object]:
    return {"policy": environment.resolve("review_policy")}


selected_policy = ["Use direct evidence."]


def respond(call: StepCall) -> StepResponse:
    context = nh.get_step_context()
    if call.natural_program == "parent":
        context.record_assignment("parent_temporary", 42)
        with nh.scope(task_references=select_shared_references(context.environment)):
            child(6)
        assert context.environment.resolve("parent_temporary") == 42
    else:
        assert context.environment.resolve("value") == 6
        assert context.environment.resolve("policy") is selected_policy
        assert context.environment.describe("policy")["origin"] == "task"
        assert context.environment.describe("conversation_input")["origin"] is None
        assert context.environment.describe("parent_temporary")["origin"] is None
    return pass_response()


with nh.run(CallbackExecutor(respond)):
    execute_step(
        StepDefinition("parent"),
        name_to_value={"conversation_input": "parent conversation", "review_policy": selected_policy},
        write_binding=lambda name, value: None,
    )

print("child_value=6; policy_shared=True; parent_input_visible=False")
```
<!-- /runnable-example:shared_values_sync -->

The asynchronous example uses the same selection rule and awaits the child inside the sharing scope.
An ordinary coroutine uses the context where it runs; a Task uses its selected creation context.
Scopes restore their references on normal exit, exceptions, and cancellation, and separate concurrent contexts can supply different references.

<!-- runnable-example:shared_values_async -->
```py
import asyncio

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepDefinition, StepEnvironment, StepRequest, execute_step_async


@nh.function
async def child(value: int) -> None:
    """execute
    Inspect <value> using <policy>.
    """


def select_shared_references(environment: StepEnvironment) -> dict[str, object]:
    return {"policy": environment.resolve("review_policy")}


selected_policy = ["Use direct evidence."]


class Executor:
    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        context = request.step_context
        if request.processed_natural_program == "parent":
            context.record_assignment("parent_temporary", 42)
            with nh.scope(task_references=select_shared_references(context.environment)):
                await child(6)
            assert context.environment.resolve("parent_temporary") == 42
        else:
            assert context.environment.resolve("value") == 6
            assert context.environment.resolve("policy") is selected_policy
            assert context.environment.describe("policy")["origin"] == "task"
            assert context.environment.describe("conversation_input")["origin"] is None
            assert context.environment.describe("parent_temporary")["origin"] is None
        return StepCandidate(outcome=Pass())


async def main() -> None:
    with nh.run(Executor()):
        await execute_step_async(
            StepDefinition("parent"),
            name_to_value={"conversation_input": "parent conversation", "review_policy": selected_policy},
            write_binding=lambda name, value: None,
        )


asyncio.run(main())
print("child_value=6; policy_shared=True; parent_input_visible=False")
```
<!-- /runnable-example:shared_values_async -->

Select only the references that belong to the child's contract.
If an application deliberately wants all of a step's bound working names, `environment.snapshot_working_state()` returns a fresh dictionary of Python working locals, live cell values, and tracked assignments, excluding globals, builtins, task references, and runtime names.
Neither that snapshot nor a selection helper shares anything until the application passes its result to a call or scope.
Scope mappings are shallow copies; shared objects retain identity and may be mutated through their normal Python interfaces.

This is a name-resolution contract, not a security sandbox.
The child's own Python globals and real closure cells remain accessible, and explicitly supplied objects can expose other state.
Parent-step and function-call relationships, executor selection, usage accounting, inspection, and lifecycle remain intact across sharing scopes.
A call handler may open such a scope around `proceed()` or `await proceed()` without rewriting or redecorating the called functions.

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
Use a [function-call handler](host-integration.md#whole-function-admission) to observe that eventual function outcome.
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


def inspect_operation(operation: OperationRequest) -> nh.oversight.OperationDecision:
    if operation.kind == "native_tool" and operation.tool_name == "delete_file":
        return nh.oversight.RejectOperation("Deletion is unavailable.", "Choose an operation that preserves the file.")
    return nh.oversight.Accept()


def inspect_step_commit(commit: nh.oversight.StepCommit) -> nh.oversight.StepCommitDecision:
    if isinstance(commit.outcome, Return) and commit.outcome.value is None:
        return nh.oversight.AbortStep("A result is required.")
    return nh.oversight.Accept()


oversight = nh.oversight.Oversight(
    inspect_operation=inspect_operation,
    inspect_step_commit=inspect_step_commit,
)
```

Install the configuration with `nh.scope(oversight=oversight)` inside an active run.
Operation inspection precedes execution and cannot undo earlier side effects.
`RejectOperation` returns deliberate feedback without executing that operation, allowing another choice within the same step.
`AbortStep` terminates the current step; catching its exception cannot make that step succeed.
The [operation feedback guide](operation-feedback.md) covers synchronous/asynchronous hooks, custom executors, model correction, and backend limits.
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

model = OpenAIResponsesModel("gpt-6-luna", provider=OpenAIProvider(api_key=credentials.api_key))
configuration = nh.StepExecutorConfiguration(
    execution_model=model,
    execution_model_settings={"openai_store": False},
    tokenizer_encoding="o200k_base",
)
executor = nh.AgentStepExecutor(configuration=configuration)
with nh.run(executor):
    result = workflow()
```

The host supplies `credentials` and owns the Model's client lifetime.
`nh.scope(execution_model=another_model)` selects a borrowed Model while retaining the other configuration fields, then restores the parent executor on exit.
External agents supplied through `from_agent` own `execute` model selection; `decision_model` remains independently configurable through `run` and `scope`.

Live Models cannot be serialized as settings; use `configuration.model_dump(exclude={"execution_model", "decision_model"})` for a partial export.
See the [configuration contract](specification.md#52-configuration) for ownership, serialization, and setting precedence.

## Mixing executors

Use `nh.scope(step_executor=...)` to switch executors within a single run. This is the standard pattern for mixing a cheap classifier with a deep autonomous step:

```py
fast_executor = nh.AgentStepExecutor(
    configuration=nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna"),
)

deep_executor = nh.AgentStepExecutor(
    configuration=nh.StepExecutorConfiguration(execution_model="codex:gpt-6.1-sol"),
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
    execution_model="openai-responses:gpt-6-luna",
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
For admission between requests within an unfinished block, use [request usage and admission](host-integration.md#request-usage-and-admission).

## Model selection

`execution_model=` and `decision_model=` independently change their respective models; `step_executor_configuration=` replaces the whole configuration.
Omission or `nh.UNSET` inherits; individual model selections cannot accompany a full configuration replacement.
The active executor resolves selection through `ConfigurableStepExecutor`; a host wrapper must return a wrapper retaining its controls.
Unsupported executors fail before model requests, and custom `AgentStepExecutor` subclasses must explicitly implement resolution.

```python
@nh.function
async def child() -> int:
    with nh.scope(execution_model="openai-responses:gpt-6-luna"):
        """execute
        Return the integer 17.
        """
        return 0


with nh.run(executor), nh.scope(capabilities=[request_observer]):
    with nh.scope(execution_model=borrowed_model):
        result = parent()
```

The decorator carries no model declaration and requires no credentials.
Model selection is applied when entering a run or scope, and scope exit restores the parent settings.
The same meter, capabilities, oversight, lifecycle, task references, and call handler survive model-only selection.
Explicit replacement, clearing hooks, or starting another run remains application-controlled.

Host model policies, whole-function admission, request accounting, and interruption cleanup are covered in [Host integration](host-integration.md).

## Next steps

Continue to **[Patterns](patterns.md)** for outcomes, error handling, async, cross-block composition, resilience, and common mistakes.
