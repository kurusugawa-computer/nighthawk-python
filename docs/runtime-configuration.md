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
```

Parameters:

- `step_executor_configuration`: replace the entire configuration.
- `step_executor_configuration_patch`: partially override specific fields.
- `step_executor`: replace the step executor entirely.
- `system_prompt_suffix_fragment`: append text to the system prompt for the scope.
- `user_prompt_suffix_fragment`: append text to the user prompt for the scope.

Use `step_executor_configuration_patch` for single-field changes (e.g., switching models). Use `step_executor_configuration` when all fields need explicit values. The context manager yields the resolved `StepExecutor` for the scope.

`StepExecutorConfiguration` also accepts `system_prompt_suffix_fragments` and `user_prompt_suffix_fragments` (tuples of strings) as baseline suffix fragments that apply to all steps in the run. Scope-level fragments are appended after configuration-level fragments.

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

The LOCALS and GLOBALS sections are bounded by token and item limits configured via `StepContextLimits`. When a limit is reached, remaining entries are omitted and a `<snipped>` marker is appended.

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

## Next steps

Continue to **[Patterns](patterns.md)** for outcomes, error handling, async, cross-block composition, resilience, and common mistakes.
