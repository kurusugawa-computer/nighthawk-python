# Nighthawk roadmap

These are exploratory directions.
The [specification](specification.md) defines supported behavior; Python owns orchestration, and hosts own isolation and durable records.
The [philosophy](philosophy.md) guides design decisions, and [design drivers](design-drivers.md) record application needs, findings, and evidence criteria.
Each investigation should identify the uncertainty to resolve and evidence that would change a decision.
Record findings with their scenario and evaluation evidence, then revise or retire the investigation here.

## Composition and application evidence

### Semantic checks composed with execution hooks

Can existing hooks support semantic checks with understandable failure and recursion behavior?
Test small examples combining Natural findings, deterministic checks, and an application policy at an execution boundary.
Start with existing function composition and oversight hooks.
Investigate nested Natural checks, inherited policy, unintended hook recursion, rejection, and checker failures before proposing new runtime primitives.
Distinguish applying a check from establishing the correctness of its judgment.

### Evidence from representative applications

Where does Nighthawk reduce construction and revision burden, and where does it add complexity?
Use the [Markdown knowledge-management scenario](design-drivers.md#markdown-knowledge-management) to identify concrete composition gaps.
Keep its detailed design, knowledge collection, and operational rules in application-owned sources.
Bring back small examples, findings, and contract requirements, with an explicit verification status.
Compare proposed abstractions with research, business, and simulator-backed workflows to assess generality.
Evaluation and simulation systems remain connected through application functions and libraries.

### Agent authorship and cumulative reuse

Can agents reuse and improve functions from earlier tasks while preserving accepted behavior?
Use the [construction and reuse scenario](design-drivers.md#agents-building-and-improving-reusable-functions) to assess discovery, contracts, context, and failure feedback across representative tasks.
Compare construction and revision success, assessment of results and effects, and overall complexity with ordinary Python and model libraries.

### Development tooling as an application

Do semantic checks for documentation and development workflows produce useful findings at acceptable review burden?
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

Provide conventions for limiting include paths and template access within authorized Natural sources, whether repository-managed or generated at runtime.

### Support for program authorization and revision evidence

Use the [in-task revision scenario](design-drivers.md#agents-revising-programs-during-a-task) to evaluate reusable helpers for host authorization and revision evidence under the [trusted-program contract](specification.md#3-hard-constraints).
Evaluate whether reusable helpers reduce the work of maintaining [revision evidence](embedded-execution.md#record-revision-evidence) without prescribing an application's policy or storage.
Exercise embedded execution without function interception, helper-only revisions, and changes of execution context.
Start with existing boundaries and small examples; apply the [Core evidence criteria](design-drivers.md#from-findings-to-design-decisions) to any unmet execution need.

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

Investigate propagating selected context-variable rebindings back from bridge threads while preserving [shared-value and expression-lifetime boundaries](specification.md#813-shared-values-and-expression-lifetime).

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
