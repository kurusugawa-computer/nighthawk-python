# Nighthawk roadmap

This roadmap is intentionally future-facing.

- It describes ideas and desired directions.
- It avoids implementation details.
- It should not restate what is already implemented today.

## Prompt and context

### Prompt context: locals digest (future)

Introduce an optional "locals digest": a very short, high-signal summary of the current evaluation locals.

Motivation:

- Reduce prompt size while keeping the most important state salient.
- Provide a stable place for hosts to inject their own summarization strategy.

Non-goals:

- This is not a security feature.
- This is not a substitute for structured memory.

### Prompt context improvements (future)

- More explicit control over what appears in prompt context.
- Better structured rendering for common Python values.
- Clearer contracts for truncation and prioritization when context budgets are exceeded.

### Model-capability-aware prompting (future)

Introduce tiered prompt strategies that adapt to model capability. Weaker models may need explicit examples and constraints in step contract prompts, while stronger models benefit from concise instructions that maximize freedom. Consider this when a single prompt cannot serve both tiers without measurable tradeoffs (e.g. detailed guidance consuming significant context budget, or constraining stronger models' output quality).

## Security and trust boundaries

### Template preprocessing controls (future)

- Make it easier for hosts to limit what templates can access.
- Provide safer conventions for include helpers (path allowlists, traversal protection).

### Sandboxing and isolation (future)

- Sandboxing for expression evaluation.
- Restricting template preprocessing evaluation.
- Path allowlists and traversal protection for include helpers.

## Resilience and observability

### Resilience observability (future)

Emit OpenTelemetry spans and events from resilience primitives so that retry attempts, fallback transitions, vote aggregation, and circuit breaker state changes are visible in the same trace as the Natural block execution they wrap.

Motivation:

- Production debugging requires knowing *why* a result was produced (first attempt? third retry? fallback to backup model?).
- Retry and fallback behavior is currently visible only through `nighthawk` logger output, which is not correlated with trace context.

Considerations:

- Resilience primitives are function transformers independent of Natural blocks. Span emission should be optional or zero-cost when no tracer is configured.
- Define which attributes to record (attempt count, elapsed time per attempt, decide function output distribution for vote, circuit state transitions).

### Resilience defaults via scope (future)

Allow hosts to set default resilience policies (retry attempts, timeout, fallback chain) at the `nighthawk.scope()` level, so that all Natural block executions within a scope inherit a baseline policy without per-call wrapping.

Motivation:

- Production deployments often want uniform retry/timeout policies across many Natural blocks. Per-function wrapping is verbose and error-prone.
- Scope-level defaults compose naturally with per-call overrides: the host sets a baseline, individual calls can narrow or widen.

Considerations:

- This may require resilience awareness in the step executor or runner, crossing the current boundary where resilience operates purely outside Natural block execution.

## Runtime

### Persistence (future)

- Persisting memory across processes (file-based snapshotting, etc.).
- Persisting or reconstructing workspace objects (likely partial only).

### Execution context propagation (future)

- Propagate execution context and step-executor context across tool execution boundaries when tools run in different threads or processes.
- Define an explicit serialization/propagation mechanism for multi-process execution.

### Async bridge ContextVar propagation (future)

The `run_coroutine_synchronously` bridge copies `contextvars` into a background thread, but changes made inside the thread are not propagated back to the caller. This is safe today because mutable state flows through shared references (e.g. `StepContext.step_locals`), but future features that rely on ContextVar side-effects across the sync-async boundary will need an explicit propagation mechanism.

## Implementation hardening

### f-string binding validation robustness (future)

The f-string binding span validation uses a NUL byte (`\x00`) as a placeholder for formatted-value boundaries. This is safe in practice but could theoretically be confused by f-string expressions that produce NUL bytes. A more robust approach would use AST position information instead of string scanning.

## Coding agent backends

### Backend-agnostic structured output (future)

Resolve backend-specific limitations in combining MCP tool exposure with structured output (e.g., Codex CLI "stream disconnected" errors when MCP tools and `--output-schema` are used together).

### New backend integration criteria (future)

Define criteria and a minimal interface for adding new coding agent backends, reducing integration effort for future CLI agents.

### Cross-backend skill portability (future)

Improve conventions for sharing skill definitions across backends beyond symlinks. Consider a backend-neutral skill metadata format that each backend adapter can translate to its native convention.

## Open questions

- How to best represent tool results in the prompt for robust reasoning.
- How to debug Natural blocks deterministically (unit testing is addressed via `nighthawk.testing`; debugging the LLM's reasoning path remains open).
- How to best integrate resilience observability (retry/fallback/vote spans) with the existing step-level trace hierarchy without excessive span noise.
