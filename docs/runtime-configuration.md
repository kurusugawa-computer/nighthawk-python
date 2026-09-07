# Runtime configuration

> This page assumes you have completed [Executors](executors.md).

This page covers how to configure execution at runtime: scope composition, prompt suffix fragments, context limits, JSON rendering, and execution identity. These settings are independent of executor choice and apply equally to Pydantic AI providers and coding agent backends.

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
        implicit_references={},
    ):
        formal_summary(text)

    # Replace the step executor entirely for a section
    with nh.scope(step_executor=another_executor):
        specialized_step(data)

    # Add implicit global references for this scope
    with nh.scope(implicit_references={"search_repository": search_repository}):
        typed_labeling_step(ticket_text)

    # Add synchronous oversight for this scope
    with nh.scope(
        oversight=nh.oversight.Oversight(
            inspect_tool_call=inspect_tool_call,
            inspect_step_commit=inspect_step_commit,
        )
    ):
        inspected_step(ticket_text)
```

Parameters:

- `step_executor_configuration`: replace the entire configuration.
- `step_executor`: replace the step executor entirely.
- `usage_meter`: replace the `UsageMeter` that steps in this scope record into. See [Usage metering](#usage-metering).
- `oversight`: scope-level synchronous tool-call, return-expression, and resolved step-commit inspection hooks.
- `lifecycle`: synchronous terminal delivery after caller assignments.
- `system_prompt_suffix_fragments`: scope-level system suffix fragments.
- `user_prompt_suffix_fragments`: scope-level user suffix fragments.
- `implicit_references`: scope-level implicit global references.
- `tools`: scope-level native tools. See [Scoped tools](#scoped-tools).
- `capabilities`: scope-level Pydantic AI capabilities. See [Pydantic AI capabilities in scopes](#pydantic-ai-capabilities-in-scopes).

Composition is selected by each supplied value:

- Omission or `nh.UNSET` inherits the parent value.
- Ordinary values replace; `[]` and `{}` clear their collections.
- `nh.Extend(sequence)` appends prompt fragments, tools, or capabilities.
- `nh.Merge(mapping)` merges implicit references, rejecting a same-name value unless it is the identical Python object.

`oversight` and `lifecycle` accept `None`, which clears their hooks. Other scope fields reject it. `usage_meter` replaces the enclosing meter without forwarding totals. Resolution errors leave the parent context intact.

The context manager yields the resolved `StepExecutor` for the scope.

`StepExecutorConfiguration` also accepts `system_prompt_suffix_fragments` and `user_prompt_suffix_fragments` (tuples of strings) as baseline suffix fragments for the whole run. Resolved scope fragments follow configuration-level baseline fragments. Clearing scope fragments preserves that configuration baseline.

System prompt suffix fragments are rendered with the same `$tool_result_max_tokens` placeholder used by built-in prompt templates. Use `$$tool_result_max_tokens` if the literal text must appear in the final prompt.

Text-projected backends that expose Nighthawk tools automatically add a short tool-result preview warning to the system prompt. You do not need to add a separate preview warning fragment for coding-agent or other text-projected tool transports.

## Scoped implicit references

`implicit_references` can inject global helper functions as step capabilities:

```py
def search_repository(query: str) -> list[str]: ...


with nh.run(step_executor):
    with nh.scope(implicit_references={"search_repository": search_repository}):
        triage_issue(ticket_text)
```

Mappings replace inherited references. Use `nh.Merge({"child": search_repository})` to add references with identity-based conflict checks.

```py
with nh.run(step_executor), nh.scope(implicit_references={"parent": search_repository}):
    with nh.scope(implicit_references={}):
        triage_issue(ticket_text)
```

The inner scope above clears inherited implicit references.

```py
with nh.scope(implicit_references={"child": search_repository}):
    triage_issue(ticket_text)
```

This fully replaces inherited references with the provided mapping.

```py
with nh.scope(implicit_references=nh.UNSET):
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

`implicit_references` and `tools` have distinct roles. `implicit_references` injects Python names into the block's namespace: they render as signature lines and the model invokes them through Nighthawk's expression tool, at the cost of one line of prompt context. `tools` exposes callables through native tool calling: each tool adds a JSON Schema to every model request, passes through Nighthawk's tool boundary and `inspect_tool_call`, and is served to coding-agent backends over MCP. Prefer `implicit_references` for Python helpers. Reserve `tools` for cases that need native tool calling, such as strict argument schemas, Pydantic AI tool features (`ModelRetry`, `ApprovalRequired`, timeouts), or first-class tool exposure to a coding agent. Do not expose the same function through both.

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

- `nh.get_implicit_references()` returns a `Mapping[str, object]` snapshot of implicit references.
- `nh.get_system_prompt_suffix_fragments()` returns the system prompt suffix fragments as a `tuple[str, ...]`.
- `nh.get_user_prompt_suffix_fragments()` returns the user prompt suffix fragments as a `tuple[str, ...]`.
- `nh.get_tools()` returns the scoped native tools as a `tuple[Tool[StepContext], ...]`, excluding built-in tools.
- `nh.get_capabilities()` returns the scoped Pydantic AI capabilities as a tuple.
- `nh.get_lifecycle()` returns the active `StepLifecycle`, or `None`.
- `nh.get_oversight()` returns the active `Oversight`, or `None` when none is installed.

The prompt suffix getters return only fragments accumulated via `nh.scope(...)`; configuration-level baseline fragments from `StepExecutorConfiguration` are not included.

Use these when composing nested scopes from a host helper: read the parent state, derive a subset, then re-enter via `nh.scope(...)`. Both narrowing (keeping a subset) and masking (removing specific keys) are expressed by deriving a new mapping and passing it as an ordinary mapping.

```py
with nh.run(step_executor):
    with nh.scope(implicit_references={"a": helper_a, "b": helper_b}):
        current = nh.get_implicit_references()
        narrowed = {name: value for name, value in current.items() if name != "b"}
        with nh.scope(implicit_references=narrowed):
            triage_issue(ticket_text)
```

All getters listed above, including `nh.get_oversight()`, require an active run context. Outside `nh.run()` they raise `NighthawkError`, matching `nh.get_step_executor()` semantics. Catch `NighthawkError` if a helper needs to detect the absence of a run.

## Record completed executions

Install `nh.lifecycle.StepLifecycle` to record actual execution endings, including preparation, validation, and return-await failures. The callback runs after generated assignments and can raise a host exception referring to its stored record. Return-expression approval is available separately through `Oversight.inspect_return_expression`; core retains evaluation and validation.

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

For a host ledger, use the executor boundary for admission and execution accounting, `inspect_step_commit` for approval before assignment, and StepLifecycle for terminal recording and exception translation. Correlate them with `execution_reference.step_execution_id`, which is unique per invocation; `source_location` only groups executions of the same source block. Preparation failures may have no admission record.

If an earlier host boundary already stored a failure and raised its public exception, the lifecycle handler still receives StepFailed. Skip a duplicate append only after verifying that exception refers to the stored event for this same execution, then rethrow the same exception. Core preserves its original cause. Merely returning from the handler results in an ExecutionError wrapper; suppressing all exceptions of the host type would incorrectly omit parent events for failed nested executions.

At a host-controlled admission boundary, `nh.get_lifecycle() is expected_lifecycle` can verify that the required callback is configured. If it has been cleared, record the bypass through that still-active boundary: the missing lifecycle cannot record its own absence. Delivery is one in-process attempt with a configured handler, not guaranteed persistence. A callback exception after successful assignment propagates without undoing writes; host delivery-failure and anomaly records are separate from the completed core record. The full rules are in [terminal delivery semantics](specification.md#104-terminal-delivery-and-host-ledgers).

## Synchronous oversight in scopes

Use `nh.scope(oversight=...)` when the host needs synchronous inspection around tool calls or a final rewrite/reject checkpoint before Nighthawk commits a step result.

```py
def inspect_tool_call(tool_call: nh.oversight.ToolCall) -> nh.oversight.ToolCallDecision:
    if tool_call.tool_name == "delete_file":
        return nh.oversight.Reject("Deletion must be approved by a human.")
    return nh.oversight.Accept()


def inspect_step_commit(step_commit: nh.oversight.StepCommit) -> nh.oversight.StepCommitDecision:
    if "result" in step_commit.binding_name_to_value:
        return nh.oversight.Rewrite(binding_name_to_value={"result": "reviewed"})
    return nh.oversight.Accept()


with nh.run(step_executor):
    with nh.scope(
        oversight=nh.oversight.Oversight(
            inspect_tool_call=inspect_tool_call,
            inspect_step_commit=inspect_step_commit,
        )
    ):
        inspected_step(ticket_text)
```

`inspect_step_commit` receives one validated candidate. Its `binding_name_to_value` contains validated writes; `outcome` is `Pass`, `Return(value=...)`, `Break`, `Continue`, or `Raise(message=..., error_type=...)`. `Return(value=None)` is distinct from `Pass()`. Invalid initial results fail before inspection.

`Rewrite` accepts `outcome`, `binding_name_to_value` (a complete replacement mapping), or `return_value`. Omitted fields use `nh.UNSET`. `Rewrite(return_value=None)` explicitly returns `None` when the function annotation permits it and the candidate already returns. Use `Rewrite(outcome=nh.oversight.Return(value=7))` to change an allowed pass to return. Supplying both return forms is invalid.

Acceptance preserves validated values without repeating validators. Replacements are validated before any final assignment, and inspection is not repeated. Bindings-only rewrites retain the already resolved return; its expression is evaluated and, when appropriate, awaited only once.

Inspection mappings are shallow read-only reference views: their entries retain Python types and identity. Trusted hooks must not mutate referenced objects; use `Rewrite`. Copy or serialize explicitly for durable history. Rejection cannot roll back effects of tools, validators, or return expressions. See [Specification](specification.md#102-host-commit-boundary) for the complete contract.

Tool rejections are returned to the model as a recoverable observation. On preview-based paths this appears with `error.kind == "oversight"` in the projected preview; provider-backed paths that use Pydantic AI's standard retry loop may instead surface the same structured details as a retry prompt whose final line is compact JSON. Step rejections produce a failed terminal record and raise `nh.ExecutionError` chained from `nh.oversight.OversightRejectedError`, unless the lifecycle callback supplies a host exception. For the normative boundary rule on which tool-call failures are projected back to the model versus propagated as host exceptions, see [Specification Section 8.3](specification.md#83-tools-available-to-the-llm).

For return approval, a host may parse `ReturnExpression.expression` and permit only a bare name present in `validated_binding_name_to_value`. Declaration alone does not prove that this candidate supplied a validated value. This policy does not skip return validation, prevent awaiting an awaitable binding, or keep the return synchronized with later binding rewrites. Arbitrary constants also bypass write bindings. Keep `deny: [return]` if the host's value policy has not admitted these behaviors; see [return approval semantics](specification.md#103-return-expression-approval).

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
execution_reference.step_execution_id  # Unique per invocation; None outside a step
execution_reference.source_location  # Source module and line; None outside a step
```

Use `run_id` to correlate distributed agent processes in logs and traces. Use `scope_id` to identify the current logical execution context. See [Specification Section 10](specification.md#10-runtime-scoping) for the full specification and [Verification: observability](verification.md#observability) for tracing integration.

## Usage metering

Each `nh.run()` creates a `UsageMeter` that accumulates LLM token usage across all Natural block executions in the run. The meter is thread-safe and updated automatically after each step. Pass `nh.run(step_executor, usage_meter=meter)` to supply your own meter, and `nh.scope(usage_meter=meter)` to meter a nested section in isolation: steps inside the scope record into the scoped meter only, and totals are not forwarded to the enclosing meter.

```py
with nh.run(step_executor):
    meter = nh.get_usage_meter()
    meter.total_tokens  # cumulative tokens
    meter.snapshot()  # independent RunUsage copy of current totals
```

`get_usage_meter()` requires an active `nh.run()` context. Use the meter to inspect cumulative cost at decision points -- for example, to choose a cheaper model mid-pipeline when spend is high. For automatic budget enforcement, see [Patterns: Budget](patterns.md#budget).

## Next steps

Continue to **[Patterns](patterns.md)** for outcomes, error handling, async, cross-block composition, resilience, and common mistakes.
