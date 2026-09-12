# Nighthawk roadmap

This roadmap is intentionally future-facing.

- It describes ideas and desired directions.
- It avoids implementation details.
- It should not restate what is already implemented today.

The [specification](specification.md) owns runtime contracts; the ideas below are exploratory extensions.
External isolation and durable application records remain host/backend concerns.

## Prompt and context

### Prompt context: locals digest

Explore reusable summarization strategies built on the existing [context builder](specification.md#824-replaceable-context-construction).
The extension would provide a short summary of task-relevant working state.

Motivation:

- Reduce prompt size while keeping the most important state salient.
- Provide a stable place for hosts to inject their own summarization strategy.

Non-goals:

- This is not a security feature.
- This is not a substitute for structured memory.

### Domain-specific context descriptions

Explore reusable descriptions for domain values that remain opaque under the existing bounded, non-executing [inspection contract](specification.md#825-value-inspection-and-name-discovery).
Preserve omitted-name retrieval, provenance, and live-value access independently of richer descriptions.

### Model-capability-aware prompting

Introduce tiered prompt strategies that adapt to model capability. Weaker models may need explicit examples and constraints in step contract prompts, while stronger models benefit from concise instructions that maximize freedom. Consider this when a single prompt cannot serve both tiers without measurable tradeoffs (e.g. detailed guidance consuming significant context budget, or constraining stronger models' output quality).

## Security and trust boundaries

### Template preprocessing controls

- Make it easier for hosts to limit what templates can access.
- Provide safer conventions for include helpers (path allowlists, traversal protection).

### External isolation

Explore host/backend integration guidance for process isolation and permissions.
Natural sources and template preprocessing remain trusted program assets.
Expression sandboxing and resistance to hostile generated Python are withdrawn
as core targets; the preprocessing conveniences above do not change that trust
boundary or establish a sandbox guarantee.

## Resilience and observability

### Resilience observability coverage

Complete OpenTelemetry coverage for resilience primitives that still rely on logger-only diagnostics, and align event semantics across primitives so traces remain queryable across backends.

Motivation:

- Production debugging still has blind spots around fallback selection, vote outcome distribution, and circuit state recovery transitions.
- Existing resilience telemetry uses different granularity and attribute naming, which makes cross-primitive analysis harder than it should be.

Considerations:

- Resilience primitives are function transformers independent of Natural blocks. Telemetry should remain optional or near-zero overhead when no tracer is configured.
- Define a shared attribute/event schema for attempts, decisions, and state transitions while avoiding excessive span noise.

### Resilience defaults via scope

Python function transformers remain the [retry mechanism](patterns.md#resilience-patterns), with host-selected retry boundaries.
Any scope-default proposal must preserve that ownership and must not introduce automatic replay after partial effects or lifecycle-delivery failure.

Allow hosts to set default resilience policies (retry attempts, timeout, fallback chain) at the `nighthawk.scope()` level, so that all Natural block executions within a scope inherit a baseline policy without per-call wrapping.

Motivation:

- Production deployments often want uniform retry/timeout policies across many Natural blocks. Per-function wrapping is verbose and error-prone.
- Scope-level defaults compose naturally with per-call overrides: the host sets a baseline, individual calls can narrow or widen.

Considerations:

- This may require resilience awareness in the step executor or runner, crossing the current boundary where resilience operates purely outside Natural block execution.

## Runtime

### Host-owned persistence

Explore examples of hosts persisting task history, application snapshots, and
execution records. Durable reconstruction of Natural workspaces, suspended Python
execution, and process-restart continuation are withdrawn as core targets. Host
storage must not imply automatic replay of operations with possible side effects.

### Execution context propagation

- Define an explicit serialization/propagation mechanism for multi-process execution beyond the existing in-process thread and event-loop bridges.

Multi-process propagation is exploratory and concerns explicitly serialized host
data and correlation metadata, not implicit transfer of live workspace objects
or reconstruction of a suspended step.

### Async bridge ContextVar propagation

Explore explicit propagation back to the caller for future features relying on context-variable rebinding inside a bridge thread.
The existing bridge propagates execution context into supported thread calls and shares referenced objects; it does not propagate arbitrary context-variable rebinding back.
Any extension must retain the [parent lifetime and shared-value boundaries](specification.md#813-parent-lifetime-and-expression-lifetime).

## Implementation hardening

### f-string binding validation robustness

The f-string binding span validation uses a NUL byte (`\x00`) as a placeholder for formatted-value boundaries. This is safe in practice but could theoretically be confused by f-string expressions that produce NUL bytes. A more robust approach would use AST position information instead of string scanning.

## Coding agent backends

### Backend-agnostic structured output

Resolve backend-specific limitations in combining MCP tool exposure with structured output (e.g., Codex CLI "stream disconnected" errors when MCP tools and `--output-schema` are used together).

### MCP document transport capability

Consider native MCP document transport only when a backend can advertise and verify stable document-resource support. Until then, document content should remain text-projected so coding-agent backends preserve predictable fallback behavior.

Non-goal:

- Native video transport is intentionally not on the short-term roadmap because video-modal API and client requirements are still unstable.

### New backend integration criteria

Define criteria and a minimal interface for adding new coding agent backends, reducing integration effort for future CLI agents.

### Cross-backend skill portability

Improve conventions for sharing skill definitions across backends beyond symlinks. Consider a backend-neutral skill metadata format that each backend adapter can translate to its native convention.

## Open questions

- Whether statement/iteration conveniences or a redesigned completion interface improve semantic reliability enough to justify their complexity; neither is an accepted runtime requirement.
- Which task-specific guidance improves result use under the existing bounded inspection and discovery contract.
- How to debug Natural blocks deterministically (unit testing is addressed via `nighthawk.testing`; debugging the LLM's reasoning path remains open).
- Which shared OpenTelemetry event/attribute schema should resilience primitives follow (retry, timeout, budget, vote, fallback, circuit breaker)?
- How should fallback/vote decisions be represented in traces (detail level, cardinality limits, and noise control)?
