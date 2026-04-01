# Runtime configuration

> This page assumes you have completed [Executors](executors.md).

This page covers how to configure execution at runtime: scoping, configuration patching, prompt suffix fragments, context limits, JSON rendering, and execution identity. These settings are independent of executor choice and apply equally to Pydantic AI providers and coding agent backends.

## Scoped overrides with `nh.scope()`

Use `nh.scope()` to override execution settings within an existing run. Each scope generates a new `scope_id` while keeping the current `run_id`.

```py
with nh.run(step_executor):

    # Override model for a specific section
    with nh.scope(
        step_executor_configuration_patch=nh.StepExecutorConfigurationPatch(
            model="openai-responses:gpt-5.4-mini",
        ),
    ) as scoped_executor:
        expensive_analysis(data)

    # Append a system prompt suffix for a section
    with nh.scope(
        system_prompt_suffix_fragment="Always respond in formal English.",
    ):
        formal_summary(text)

    # Replace the step executor entirely for a section
    with nh.scope(step_executor=another_executor):
        specialized_step(data)

    # Add implicit global references for this scope (merged across nested scopes)
    with nh.scope(implicit_references={"search_repository": search_repository}):
        typed_labeling_step(ticket_text)
```

Parameters:

- `step_executor_configuration`: replace the entire configuration.
- `step_executor_configuration_patch`: partially override specific fields.
- `step_executor`: replace the step executor entirely.
- `system_prompt_suffix_fragment`: append text to the system prompt for the scope.
- `user_prompt_suffix_fragment`: append text to the user prompt for the scope.
- `implicit_references`: add implicit global references for this scope as a name-to-value mapping. Nested scopes merge references additively (set union by key).

Use `step_executor_configuration` when you want a full configuration replacement for a scope.
Use `step_executor_configuration_patch` for targeted changes (for example, switching only the model).
Use `step_executor` to swap the executor implementation for that scope.
Use `system_prompt_suffix_fragment` and `user_prompt_suffix_fragment` to append one-off scope-level prompt text.
Use `implicit_references` when a step should always expose specific globals even without explicit `<name>` bindings.

The context manager yields the resolved `StepExecutor` for the scope.

`StepExecutorConfiguration` also accepts `system_prompt_suffix_fragments` and `user_prompt_suffix_fragments` (tuples of strings) as baseline suffix fragments for the whole run. Scope-level fragments are appended after configuration-level fragments.

## Additive scoped implicit references

`implicit_references` can inject global helper functions as step capabilities:

```py
def search_repository(query: str) -> list[str]: ...

with nh.run(step_executor):
    with nh.scope(implicit_references={"search_repository": search_repository}):
        triage_issue(ticket_text)
```

Nested scopes merge names additively (set union by key).

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

Each `nh.run()` generates an `ExecutionContext` with a unique `run_id` (trace root) and `scope_id`. Nested `nh.scope()` calls generate new `scope_id` values while keeping the same `run_id`.

```py
context = nh.get_execution_context()
context.run_id    # trace root -- stable across nested scopes
context.scope_id  # current scope -- changes with each nh.scope()
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
