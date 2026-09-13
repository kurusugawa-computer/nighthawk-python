# Nighthawk guide for coding agents

Use this guide when writing Python applications with Nighthawk.
The [specification](https://kurusugawa-computer.github.io/nighthawk-python/specification/) defines runtime behavior; this page selects the operational rules.

## Choose the boundary

- Python owns computation, I/O, validation, branching, retries, state, and orchestration.
- Natural blocks supply semantic judgment: interpretation, classification, generation, ranking, or context-dependent selection.
- Give each block one task and a narrow, typed output. Split independent judgments into separate calls.
- Start with a provider-backed executor. Use a coding-agent backend for blocks needing repository access, commands, or autonomous investigation.
- Natural source, imported markdown, and prompt fragments are trusted, repository-managed code. Pass user input as binding values, never as interpolated Natural source.

See [executor selection](https://kurusugawa-computer.github.io/nighthawk-python/executors/) for backend tradeoffs.

## A working template

Install `nighthawk-python` and the provider dependency:

```sh
pip install nighthawk-python "pydantic-ai-slim[openai]"
```

Set `OPENAI_API_KEY`, then save and run this Python file:

```py
from typing import Literal

import nighthawk as nh


@nh.natural_function
def classify_ticket(text: str) -> Literal["bug", "feature", "question"]:
    label: Literal["bug", "feature", "question"] = "question"
    """natural
    ---
    deny: [raise, return]
    ---
    Read <text> and set <:label> to bug, feature, or question.
    """
    return label


executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna"),
)
with nh.run(executor):
    print(classify_ticket("The application crashes when opening a file."))
```

An active run is required when a block executes or a function's model declaration resolves.
Decoration, definition lookup, and host refusal before body entry need no run.
See [Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/) for setup and credentials.

## Bindings and context

- `<name>` requires a value and prioritizes its presentation; it is not an access allowlist.
- `<:name>` declares a validated Python output slot. Annotate it explicitly; use `Literal`, collections, or Pydantic models to express the result contract.
- Prefer a block that writes outputs followed by ordinary Python checks. Use direct Natural return for leaf judgments and `deny: [pass, raise]` when return is required.
- Inputs can include lexical values, inherited parent working state, and scoped task references. Pass essential child inputs explicitly; reserve inheritance for shared policy or context.
- Child rebinding stays local. Object mutations are shared and can survive failure. Only children joined by their parent have the parent-lifetime inheritance guarantee.
- Each block starts a fresh model interaction. Carry state through Python parameters, return values, or deliberate shared objects.
- Bindings name Python variables. Dotted references can present multimodal leaves; they do not declare dotted write bindings.
- Inline f-string blocks evaluate Python expressions before prompt construction. Interpolate only trusted configuration; use bindings for user data.

See [Natural blocks](https://kurusugawa-computer.github.io/nighthawk-python/natural-blocks/) for binding syntax, structured outputs, and prompt examples.

## Python helpers

Expose small Python functions with precise signatures and short docstrings.
Use module-level helpers for stable capabilities and parameters for invocation-specific values.
A callable passed through a parameter retains its live signature even if that parameter is annotated as `object` or `Any`; precise annotations also help Python tooling and readers.
Metadata discovery is best-effort, so make essential dependencies explicit.

Reduce incidental locals and large previews before increasing `StepContextLimits`.
A `<snipped>` marker means presentation was truncated; values can be retrieved through discovery or helpers.
Even explicit multimodal references can be omitted when the presentation budget is exhausted.

See [binding function design](https://kurusugawa-computer.github.io/nighthawk-python/natural-blocks/#designing-binding-functions).

## Scopes and models

- `nh.run(executor)` establishes an executor and usage meter. `nh.scope(...)` composes settings within that run and restores its parent on exit.
- Omission or `nh.UNSET` inherits; ordinary values replace; empty collections clear. `nh.Extend` appends sequences, and `nh.Merge` merges task references with identity-based conflict checks.
- `oversight=None` and `lifecycle=None` clear those hooks. Other scope fields reject `None`.
- `model=...` selects only the model in a scope or `@nh.natural_function(...)` declaration. `step_executor_configuration=...` replaces all configuration fields. Do not supply both.
- Declarations are inert until execution. Selection applies before the body and restores the parent on success, failure, or cancellation.
- Model selection delegates to the active executor's `ConfigurableStepExecutor` resolver. Host wrappers and `AgentStepExecutor` subclasses must implement preserving resolution; unsupported executors fail explicitly.
- Use `task_references` for injected helpers and `step_executor` for deliberate executor replacement. Starting a nested run resets scoped defaults; function-call interception is independent.

The [runtime configuration guide](https://kurusugawa-computer.github.io/nighthawk-python/runtime-configuration/) contains examples for model selection, task injection, and mixed executors.

## Host admission and completion

Use `nh.function_call_scope(handler)` to refuse a function before Python body effects or declared model resolution.
Implement `invoke(call, proceed)` using `proceed()` and `invoke_async(call, proceed)` using `await proceed()`.
Refuse by raising; a continuation must finish once within its handler's active Task and thread.
The handler may establish a run before proceeding.

`nh.get_function_call()` exposes invocation and parent Ids.
Use `nh.get_natural_function_definition()` for exact original/wrapper/transformed correspondence; `nh.get_transformed_function()` follows conventional outer wrappers for inspection.

Keep three boundaries distinct:

- Function completion includes Python `finally`, explicitly joined children, and Core-owned work. Intercepted generators are unsupported.
- Block lifecycle facts cover preparation, validation, assignments, and failures. A completed block does not prove the containing function succeeded.
- Request capabilities observe the Pydantic AI request boundary. SDK retries and coding-agent internal requests can be hidden.

Core joins its notification and synchronous-bridge cleanup; resistant cleanup may wait indefinitely.
`interruption_delivery_timeout` requests cancellation, not an overall completion deadline.
The host owns detached work and durable records.
See [execution boundaries](https://kurusugawa-computer.github.io/nighthawk-python/specification/#embedded-execution-boundaries) for context timing, interruption, and ownership details.

## Failures, async, and budgets

- Catch `nh.ExecutionError` for runtime failures; catch application exception types when helpers or Natural outcomes raise them directly.
- Reconcile partial effects before retrying. A lifecycle storage failure never justifies replaying a Natural execution.
- DSL tool expressions await awaitable results in both sync and async functions. Awaitable final Natural returns require `async def`; an ordinary synchronous Python return preserves its coroutine object without awaiting or closing it.
- Use `nighthawk.resilience` to compose retry, fallback, voting, timeout, budget, and circuit-breaker policy in Python.
- Async timeouts are cooperative. The sync timeout wrapper waits for its worker and does not copy caller context variables; prefer async execution when inheriting host scopes.
- `UsageMeter` records returned agent-run aggregates and may miss usage from failed runs. Function-level budget checks are not a strict cost cap.
- For admission within an unfinished block, keep a separate host request accumulator and refuse the next sequential request after the threshold is reached. Already admitted or concurrent work can overrun it. Never add the meter total as a second charge.
- Keep unknown model/usage metadata unknown, and distinguish failure and cancellation from successful responses.

See [workflow and resilience patterns](https://kurusugawa-computer.github.io/nighthawk-python/patterns/) and [request accounting](https://kurusugawa-computer.github.io/nighthawk-python/runtime-configuration/#request-usage-and-admission).

## Verification

Use `nighthawk.testing.ScriptedExecutor` for fixed response sequences and `CallbackExecutor` for input-dependent responses.
Assert Python control flow, output validation, call counts, selected scopes, and relevant effects.
Run opt-in provider tests for the actual prompt's semantic result; assert types, ranges, or categories instead of exact prose.
Mock success is not evidence of model judgment quality.

See [Verification](https://kurusugawa-computer.github.io/nighthawk-python/verification/) for executable tests and debugging, and [API reference](https://kurusugawa-computer.github.io/nighthawk-python/api/) for public interfaces.
