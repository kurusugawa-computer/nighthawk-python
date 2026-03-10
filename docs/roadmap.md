# Nighthawk roadmap

This roadmap is intentionally future-facing.

- It describes ideas and desired directions.
- It avoids implementation details.
- It should not restate what is already implemented today.

## Planned themes

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

### Template preprocessing controls (future)

- Make it easier for hosts to limit what templates can access.
- Provide safer conventions for include helpers (path allowlists, traversal protection).

### Security and sandboxing (future)

- Sandboxing for expression evaluation.
- Restricting template preprocessing evaluation.
- Path allowlists and traversal protection for include helpers.

### Persistence (future)

- Persisting memory across processes (file-based snapshotting, etc.).
- Persisting or reconstructing workspace objects (likely partial only).

### Execution context propagation (future)

- Propagate execution context and step-executor context across tool execution boundaries when tools run in different threads or processes.
- Define an explicit serialization/propagation mechanism for multi-process execution.

### Skills-style packaging (future)

- A directory structure similar to Claude Skills (SKILL.md, REFERENCE.md, scripts/).
- Document a minimal "Skills" packaging convention for Nighthawk.

### Async bridge ContextVar propagation (future)

The `run_coroutine_synchronously` bridge copies `contextvars` into a background thread, but changes made inside the thread are not propagated back to the caller. This is safe today because mutable state flows through shared references (e.g. `StepContext.step_locals`), but future features that rely on ContextVar side-effects across the sync-async boundary will need an explicit propagation mechanism.

### f-string binding validation robustness (future)

The f-string binding span validation uses a NUL byte (`\x00`) as a placeholder for formatted-value boundaries. This is safe in practice but could theoretically be confused by f-string expressions that produce NUL bytes. A more robust approach would use AST position information instead of string scanning.

### Reference manual (future)

Add a topic-organized reference manual (`docs/manual.md`) as a lookup-oriented companion to the tutorial. The tutorial (`docs/tutorial.md`) teaches concepts in learning order; the reference manual would organize the same material by topic for quick lookup by users who already understand the system. Candidate sections: Bindings, f-string Injection, Prompt Structure, Built-in Tools, Custom Tools, Control Flow, Carry Pattern, Scoped Configuration, Async.

### Documentation improvements (future)

- Reduce quickstart setup boilerplate (consider a convenience wrapper for the common `AgentStepExecutor.from_configuration` + `nh.run` pattern).
- Improve docstrings for API reference completeness (e.g. `get_step_executor`, `get_execution_context`).

## Open questions

- How to best represent tool results in the prompt for robust reasoning.
- How to test and debug Natural blocks deterministically.
