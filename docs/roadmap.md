# Nighthawk roadmap

Note: This roadmap is intentionally exploratory. It may include items unrelated to the current implementation, and it may contain uncertain or speculative ideas.

## Planned items

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

### Runtime context propagation (future)

- Propagate runtime context across tool execution boundaries when tools run in different threads or processes.
- Define an explicit serialization/propagation mechanism for runtime context for multi-process execution.

### Skills-style packaging

- A directory structure similar to Claude Skills (SKILL.md, REFERENCE.md, scripts/).
- Document a minimal "Skills" packaging convention for Nighthawk and how it relates to the reverse-Nightjar approach.

### Provider support

- Support multiple LLM providers beyond OpenAI.

### Runtime context

- Provide an API for an implicit runtime context (dynamic scoping) that can be set by host Python code.
- Workspace root is used for workspace tools and include resolution. Set it by entering a runtime context, for example: `with nighthawk.runtime_context(nighthawk.RuntimeContext(configuration=cfg, workspace_root=...)):`.
- Use the workspace root for include resolution and any workspace tools.

### CLI (deprioritized)

- A `nighthawk` CLI may be added later, but the preferred approach is that users choose their own Python entry point.

### Better Natural DSL

- Dotted-path bindings (e.g., `<user.name>`).
- Richer binding contracts and typed outputs per binding.
- Memory patch protocols (diff/patch rather than full replacement).
- Dotted assignment targets (e.g., `x.y.z`) beyond the current `assign` target grammar.

## Follow-ups

### LLM <-> interpreter interface

The current implementation intentionally keeps a permissive, minimal interface.

As follow-ups, consider alternatives that reduce cost and tighten semantics:

- Patch/diff updates for memory.
- More explicit, typed boundaries between LLM output, tool-local state, and interpreter-visible variables.

### Include functions (templating)

In `docs/design.md`, `include(path)` is described as an example helper for template preprocessing under a trusted-input threat model.

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

## Concept and alternative approaches

This section preserves design motivation and concepts that are either broader than the current implementation or not planned for near-term implementation.

### Nightjar style

- Write strict control flow in Python.
- Embed Natural blocks where semantic interpretation is needed.

### Skills (reverse Nightjar) style (future)

- Natural-language-first workflow.
- Mix in code only where strict procedures are needed.
- Main challenge: synchronizing execution state between the natural-language world and the code world.

### Hybrid nesting beyond docstrings (future)

- Allow nesting such as Natural -> Python -> Natural inside larger natural language documents.

### Mapping LLM state to the interpreter

- Treat the Python interpreter as the primary external memory.
- The agent writes durable state into Python variables and structured objects for inspection.

## Open questions

- How to best represent tool results in the prompt for robust reasoning.
- Whether to allow statement execution (exec) as a tool.
- How to test and debug Natural blocks deterministically.
