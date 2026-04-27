# Runtime configuration

> This page assumes you have completed [Executors](executors.md).

This page covers how to configure execution at runtime: scoping modes, prompt suffix fragments, context limits, JSON rendering, and execution identity. These settings are independent of executor choice and apply equally to Pydantic AI providers and coding agent backends.

## Scoped overrides with `nh.scope()`

Use `nh.scope()` to override execution settings within an existing run. Each scope generates a new `scope_id` while keeping the current `run_id`.

```py
with nh.run(step_executor):

    # Inherit mode (default): merge/append into current scope state
    with nh.scope(
        step_executor_configuration=nh.StepExecutorConfiguration(
            model="openai-responses:gpt-5.4-mini",
        ),
    ) as scoped_executor:
        expensive_analysis(data)

    # Replace mode: replace only explicitly provided values
    # None means "no change". [] / {} means "clear".
    with nh.scope(
        mode="replace",
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

- `mode`: scope composition mode. Default: `"inherit"`.
- `step_executor_configuration`: replace the entire configuration.
- `step_executor`: replace the step executor entirely.
- `oversight`: scope-level synchronous tool-call inspection and step-commit inspection hooks.
- `system_prompt_suffix_fragments`: scope-level system suffix fragments.
- `user_prompt_suffix_fragments`: scope-level user suffix fragments.
- `implicit_references`: scope-level implicit global references.

Mode semantics:

- `mode="inherit"` (default):
  - `system_prompt_suffix_fragments` and `user_prompt_suffix_fragments` are appended.
  - `implicit_references` are merged additively with conflict checks.
- `mode="replace"`:
  - `None` means no change.
  - Explicit `[]` or `{}` clears inherited list/dict values.
  - Explicit list/dict values (for example `[e1, e2]` or `{k1: v1, k2: v2}`) fully replace inherited values.

For `oversight`, omitted means inherit the current hooks, while explicit `None` clears them for the nested scope.

The context manager yields the resolved `StepExecutor` for the scope.

`StepExecutorConfiguration` also accepts `system_prompt_suffix_fragments` and `user_prompt_suffix_fragments` (tuples of strings) as baseline suffix fragments for the whole run. In `mode="inherit"`, scope-level fragments are appended after configuration-level fragments.

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

In `mode="inherit"` (default), nested scopes merge additively with conflict checks.
In `mode="replace"`, explicit mappings fully replace inherited mappings.

```py
with nh.run(step_executor), nh.scope(implicit_references={"parent": search_repository}):
    with nh.scope(mode="replace", implicit_references={}):
        triage_issue(ticket_text)
```

The inner scope above clears inherited implicit references.

```py
with nh.scope(mode="replace", implicit_references={"child": search_repository}):
    triage_issue(ticket_text)
```

This fully replaces inherited references with the provided mapping.

```py
with nh.scope(mode="replace", implicit_references=None):
    triage_issue(ticket_text)
```

This keeps inherited references unchanged.

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

In `mode="inherit"`, provided lists are appended.
In `mode="replace"`, provided lists fully replace inherited lists.

```py
with nh.run(step_executor), nh.scope(system_prompt_suffix_fragments=["parent"]):
    with nh.scope(mode="replace", system_prompt_suffix_fragments=["child_1", "child_2"]):
        summarize_ticket(ticket_text)
```

Pass `[]` to clear inherited fragments.

```py
with nh.scope(mode="replace", system_prompt_suffix_fragments=[]):
    summarize_ticket(ticket_text)
```

Pass `None` to keep inherited fragments unchanged.

```py
with nh.scope(mode="replace", system_prompt_suffix_fragments=None):
    summarize_ticket(ticket_text)
```

The same `replace` semantics apply to `user_prompt_suffix_fragments`.

## Reading the active scope

Within an active `nh.run()` context, three snapshot getters expose the current scope's accumulated state:

- `nh.get_implicit_references()` returns a `Mapping[str, object]` snapshot of implicit references.
- `nh.get_system_prompt_suffix_fragments()` returns the system prompt suffix fragments as a `tuple[str, ...]`.
- `nh.get_user_prompt_suffix_fragments()` returns the user prompt suffix fragments as a `tuple[str, ...]`.

The prompt suffix getters return only fragments accumulated via `nh.scope(...)`; configuration-level baseline fragments from `StepExecutorConfiguration` are not included.

Use these when composing nested scopes from a host helper: read the parent state, derive a subset, then re-enter via `nh.scope(mode="replace", ...)`. Both narrowing (keeping a subset) and masking (removing specific keys) are expressed by deriving a new mapping and passing it under `mode="replace"`.

```py
with nh.run(step_executor):
    with nh.scope(implicit_references={"a": helper_a, "b": helper_b}):
        current = nh.get_implicit_references()
        narrowed = {name: value for name, value in current.items() if name != "b"}
        with nh.scope(mode="replace", implicit_references=narrowed):
            triage_issue(ticket_text)
```

All three getters require an active run context. Outside `nh.run()` they raise `NighthawkError`, matching `nh.get_step_executor()` semantics. Catch `NighthawkError` if a helper needs to detect the absence of a run.

## Synchronous oversight in scopes

Use `nh.scope(oversight=...)` when the host needs synchronous inspection around tool calls or a final rewrite/reject checkpoint before Nighthawk commits a step result.

```py
def inspect_tool_call(tool_call: nh.oversight.ToolCall) -> nh.oversight.ToolCallDecision:
    if tool_call.tool_name == "delete_file":
        return nh.oversight.Reject("Deletion must be approved by a human.")
    return nh.oversight.Accept()


def inspect_step_commit(proposal: nh.oversight.StepCommitProposal) -> nh.oversight.StepCommitDecision:
    if "result" in proposal.proposed_binding_name_to_value:
        return nh.oversight.Rewrite(rewritten_binding_name_to_value={"result": "reviewed"})
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

Tool rejections are returned to the model as a recoverable observation. On preview-based paths this appears with `error.kind == "oversight"` in the projected preview; provider-backed paths that use Pydantic AI's standard retry loop may instead surface the same structured details as a retry prompt whose final line is compact JSON. Step rejections raise `nh.oversight.OversightRejectedError` to the host. Rewrite values still flow through the normal step finalization path. For the normative boundary rule on which tool-call failures are projected back to the model versus propagated as host exceptions, see [Specification Section 8.3](specification.md#83-tool-boundary-contract-built-in-tooling).

## Mixing executors

Use `nh.scope(step_executor=...)` to switch executors within a single run. This is the standard pattern for mixing a cheap classifier with a deep autonomous step:

```py
fast_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-mini"),
)

deep_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="codex:default"),
)

with nh.run(fast_executor):
    label = classify_ticket(text)             # fast, cheap
    with nh.scope(step_executor=deep_executor):
        diagnosis = inspect_repository(text)  # deep, autonomous
```

See [Executors](executors.md#decision-tree) for when to choose a coding agent backend over a provider-backed executor.

## Context limits

The LOCALS and GLOBALS sections are bounded by token and item limits configured via `StepContextLimits`. When a limit is reached, remaining entries are omitted and a `<snipped>` marker is appended. The underlying data remains in Python memory and is accessible through binding functions at runtime -- truncation affects prompt coherence, not data availability.

```py
configuration = nh.StepExecutorConfiguration(
    model="openai-responses:gpt-5.4-mini",
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

Each `nh.run()` generates an `ExecutionRef` with a unique `run_id` (trace root) and `scope_id`. Nested `nh.scope()` calls generate new `scope_id` values while keeping the same `run_id`.

```py
execution_ref = nh.get_execution_ref()
execution_ref.run_id    # trace root -- stable across nested scopes
execution_ref.scope_id  # current scope -- changes with each nh.scope()
execution_ref.step_id   # None outside active step execution
```

Use `run_id` to correlate distributed agent processes in logs and traces. Use `scope_id` to identify the current logical execution context. See [Specification Section 10](specification.md#10-runtime-scoping) for the full specification and [Verification: observability](verification.md#observability) for tracing integration.

## Usage metering

Each `nh.run()` creates a `UsageMeter` that accumulates LLM token usage across all Natural block executions in the run. The meter is thread-safe and updated automatically after each step.

```py
meter = nh.get_current_usage_meter()   # None outside nh.run()
if meter is not None:
    meter.total_tokens     # cumulative input + output tokens
    meter.snapshot()       # independent RunUsage copy of current totals
```

`get_current_usage_meter()` returns `None` outside an active `nh.run()` context. Use the meter to inspect cumulative cost at decision points -- for example, to choose a cheaper model mid-pipeline when spend is high. For automatic budget enforcement, see [Patterns: Budget](patterns.md#budget).

## Next steps

Continue to **[Patterns](patterns.md)** for outcomes, error handling, async, cross-block composition, resilience, and common mistakes.
