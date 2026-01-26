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

### Environment propagation (future)

- Propagate environment across tool execution boundaries when tools run in different threads or processes.
- Define an explicit serialization/propagation mechanism for environment for multi-process execution.

### Skills-style packaging (future)

- A directory structure similar to Claude Skills (SKILL.md, REFERENCE.md, scripts/).
- Document a minimal "Skills" packaging convention for Nighthawk.

### Provider support (future)

- Support multiple LLM providers beyond OpenAI.

### Implementation consistency (future)

- Unify Natural DSL binding extraction into a single source of truth.
- Strengthen typed binding behavior and reduce duplication across compilation and execution.

## Open questions

- How to best represent tool results in the prompt for robust reasoning.
- Whether to allow statement execution (exec) as a tool.
- How to test and debug Natural blocks deterministically.
