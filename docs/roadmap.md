# Nighthawk roadmap

These are exploratory directions.
The [specification](specification.md) defines supported behavior; Python owns orchestration, and hosts own isolation and durable records.
The [philosophy](philosophy.md) guides design decisions, and [design drivers](design-drivers.md) record the application needs behind them.

## Composition and application evidence

### Semantic checks composed with execution hooks

Develop small examples that combine Natural findings, deterministic checks, and an application policy at an execution boundary.
Start with existing function composition and oversight hooks.
Investigate nested Natural checks, inherited policy, unintended hook recursion, rejection, and checker failures before proposing new runtime primitives.
Distinguish applying a check from establishing the correctness of its judgment.

### Evidence from representative applications

Use the [Markdown knowledge-management scenario](design-drivers.md#markdown-knowledge-management) to identify concrete composition gaps.
Keep its detailed design, knowledge collection, and operational rules in application-owned sources.
Bring back small examples, findings, and contract requirements, with an explicit verification status.
Compare proposed abstractions with research, business, and simulator-backed workflows to assess generality.
Evaluation and simulation systems remain connected through application functions and libraries.

### Agent authorship and accumulated capability

Evaluate whether signatures, descriptions, context, results, and failure reports let agents discover, compose, and revise programs correctly.
Use representative tasks to assess whether capabilities built for one task can be reused and improved for another.
Compare construction and revision success, assessment of results and effects, and overall complexity with ordinary Python and model libraries.

### Development tooling as an application

Explore semantic checks for Core documentation and development workflows using Nighthawk functions.
Assess false positives and missed violations before adopting them in the development process.
Treat development automation and public library features as separate decisions.

## Prompt and context

### Prompt context: locals digest

Explore concise summaries of task-relevant working state through the [context builder](specification.md#824-replaceable-context-construction).
Keep live values accessible independently of their summaries.

### Domain-specific context descriptions

Extend descriptions of opaque domain values while preserving the [inspection contract](specification.md#825-value-inspection-and-name-discovery): bounded presentation, provenance, and live-value access.

### Model-capability-aware prompting

Evaluate whether model-specific guidance improves judgment quality enough to justify additional prompt and maintenance cost.

## Security and trust boundaries

### Template preprocessing controls

Provide conventions for limiting include paths and template access within trusted, repository-managed Natural sources.

### External isolation

Develop host/backend integration guidance for process isolation and permissions.

## Resilience and observability

### Resilience observability coverage

Align trace fields for attempts, decisions, and state transitions across [resilience primitives](patterns.md#resilience-patterns).
Keep telemetry optional and avoid redundant events.

### Resilience defaults via scope

Explore reusable host-selected policy defaults that compose with per-function overrides.
Preserve explicit retry boundaries and require recovery decisions after partial effects.

## Runtime

### Host-owned persistence

Provide examples of host-managed execution records and application snapshots, with explicit deduplication and recovery decisions.

### Execution context propagation

Explore explicit serialization of host data and correlation metadata for multi-process execution.

### Async bridge ContextVar propagation

Investigate propagating selected context-variable rebindings back from bridge threads while preserving [parent lifetime and shared-value boundaries](specification.md#813-parent-lifetime-and-expression-lifetime).

## Implementation hardening

### f-string binding validation robustness

Use source positions to identify interpolated spans without sentinel characters.

## Coding agent backends

### Backend-agnostic structured output

Improve diagnosis and handling of failures involving structured output and backend tools.

### MCP document transport capability

Evaluate native document transport behind an explicit backend capability.

### New backend integration criteria

Define acceptance criteria and integration examples for additional backends.

### Cross-backend skill portability

Explore shared skill metadata that adapters translate into each backend's native format.

## Open questions

- Which task-specific guidance improves use of inspection and discovery?
- Which debugging views make model decisions easier to assess alongside Python effects?
