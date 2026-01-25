# Nighthawk roadmap

Note: This roadmap is intentionally exploratory. It may include items unrelated to the current implementation, and it may contain uncertain or speculative ideas.

## Planned items

### Configuration (future)

- Environment variable support (for example `NIGHTHAWK_*`).
- Optional tool enablement flags.
- Optional memory model type selection via configuration.
- Template evaluation context controls.
- Locals summary options (max length, max frames, value summarization rules).
- Memory summary options (max length, value summarization rules).

### Security and sandboxing

- Sandboxing for workspace tools (expression evaluation in particular).
- Restricting template preprocessing evaluation.
- Path allowlists and traversal protection for include functions.
- Expand the evaluation environment safely over time (currently keeps `context_globals` minimal).

### Persistence

- Persisting memory across processes (file-based snapshotting, etc.).
- Persisting or reconstructing workspace objects (likely partial only).

### Hybrid nesting beyond docstrings

- Executing workflows that embed Python code fences inside natural language documents (a broader hybrid beyond docstrings).

### Environment propagation (future)

- Propagate environment across tool execution boundaries when tools run in different threads or processes.
- Define an explicit serialization/propagation mechanism for environment for multi-process execution.

### Skills-style packaging

- A directory structure similar to Claude Skills (SKILL.md, REFERENCE.md, scripts/).
- Document a minimal "Skills" packaging convention for Nighthawk and how it relates to the reverse-Nightjar approach.

### Provider support

- Support multiple LLM providers beyond OpenAI.

### Environment

- Provide an API for an implicit environment (dynamic scoping) that can be set by host Python code.
- Workspace root is used for workspace tools and include resolution. Set it by entering an environment, for example: `with nighthawk.environment(nighthawk.NaturalExecutionEnvironment(natural_execution_configuration=cfg.natural_execution_configuration, workspace_root=..., natural_executor=..., memory=...)):`.
- Use the workspace root for include resolution and any workspace tools.

### CLI (deprioritized)

- A `nighthawk` CLI may be added later, but the preferred approach is that users choose their own Python entry point.

### Implementation consistency

- Unify Natural DSL binding extraction into a single source of truth.
  - Today, bindings are extracted in multiple places (AST transform and Natural block parsing).
  - Target: one binding parser used for both output allowlists and any compile-time type extraction.

### Better Natural DSL

- Dotted-path bindings (e.g., `<user.name>`).
- Richer binding contracts and typed bindings per binding.
- Memory patch protocols (diff/patch rather than full replacement).
- Dotted assignment targets (e.g., `x.y.z`) beyond the current `assign` target grammar.

## Follow-ups

### LLM <-> interpreter interface

The current implementation intentionally keeps a permissive, minimal interface.

As follow-ups, consider alternatives that reduce cost and tighten semantics:

- Patch/diff updates for memory.
- More explicit, typed boundaries between LLM output, tool-local state, and interpreter-visible variables.

### Include functions (templating)

In `docs/design.md`, template preprocessing is described as evaluating templates in the caller frame environment. Hosts may choose to bind helper functions (for example `include(path)`) into the caller frame locals or globals under a trusted-input threat model.

In future, we expect include helpers to be domain-specific and to resolve paths relative to well-defined locations in the workspace, for example:

- `include_knowledge(path)`
- `include_skill(path)`

These are expected to enforce appropriate allowlists and traversal protections.

### Memory model shape

The "example" `MemoryModel` fields described in the design doc are examples.

In practice, the memory schema influences the LLM's mental model, so we expect iterative changes based on:

- domain knowledge
- prompt iteration
- analysis of LLM behavior


## Open questions

- How to best represent tool results in the prompt for robust reasoning.
- Whether to allow statement execution (exec) as a tool.
- How to test and debug Natural blocks deterministically.
