# Architecture

Nighthawk makes judgment composable through ordinary Python functions.
The package structure separates execution semantics, host authority, reusable work, and concrete integrations within one `nighthawk-python` distribution.
Use the [philosophy](philosophy.md) to evaluate design tradeoffs and the [specification](specification.md) for executable guarantees.

## Package ownership

| Package | Owns | Does not choose |
| --- | --- | --- |
| `nighthawk.core` | Natural parsing and transformation, binding contracts, execution requests and outcomes, operation and lifecycle records, scopes, usage observations, deterministic testing | Provider implementation, application goals, admission policy, persistence |
| `nighthawk.harness` | Grants, source validation and mediated dispatch, retained callable entry, resource admission, authoritative ledger facts, exact evidence resolution, causal analysis | Task strategy, judgment criteria, external service guarantees |
| `nighthawk.parts` | Composable judgments, evidence selection, conversation actions, procedure discovery, learning, bounded value views, domain capabilities, voting | Authority ceilings, durable audit guarantees, Session state |
| `nighthawk.session` | Persistent Python namespaces, cells, operator controls, retained history, publication, and optional conversation integration | The meaning of every Agent Part or every Harness run |
| `nighthawk.adapters` | Pydantic AI execution, model configuration, prompts and tool presentation, provider transports, concrete filesystem, HTTP, and artifact implementations | Core semantics and host acceptance criteria |
| `nighthawk` | The everyday `function`, `run`, and `scope` facade and explicit collection composition | A second inventory of every lower-level contract |

Applications own goals, acceptance, environments, approval, persistence, and policy.
`examples/` contains executable reference applications demonstrating those responsibilities with public library interfaces.
See [Composing Agent Parts](agent/composition.md) and [Embedding Sessions](agent/sessions.md).
The distribution has no CLI entry point; `examples/repl/` is an optional terminal application.

## Dependency direction

Core contracts depend on Python and shared validation/observability primitives.
They do not import Harness, Parts, Session, or Pydantic AI.
A custom executor can use `nighthawk.core.scoping.run(executor)` without constructing any model adapter.
Importing `nighthawk` loads none of the adapters, Harness, Parts, or Session; the facade selects the Pydantic AI implementation lazily when the caller omits an executor.

Harness consumes Core contracts and host-supplied services.
Its resource, confinement, termination, audit, and run contracts are independent of Runner's orchestration implementation.
Inert `CodeProposal` values do not validate or execute source; Harness dispatch owns those effects.
Model selection and provider construction for a Harness Runner are composed by `nighthawk.adapters.pydantic_ai.harness.model_runner`.

Parts consume Core and Harness without becoming dependencies of either.
Each domain owns its request values, result types, backend protocol, and bound capability policy.
For example, `parts.files.file_patch_capability` accepts a `FileWriter`; `adapters.files` supplies the filesystem implementation.
An application builds a `Grant` from the capabilities it actually needs.

Session composes these mechanisms and Parts.
Conversation judgments take explicit values; Session projects its cell history into conversation records and owns publication after execution.
A host can use Parts and retained generated functions without importing Session.

## Evidence and presentation

Harness ledger entries are authoritative observations of admitted execution.
`harness.evidence.resolve_evidence` resolves an exact recorded value; `analyze_run` derives causal structure and usage from those facts.
Neither decides whether an observation supports a semantic claim.

`parts.evidence.project_evidence` selects bounded, potentially lossy material for judgment.
`session.history` serves bounded operator queries over retained cell text and outcomes.
The original ledger, the selected judgment context, and the conversation history have different owners and guarantees.

Core lifecycle records carry an opaque `executor_result`.
The selected adapter owns that value's type and presentation; the Pydantic AI adapter retains native `AgentRunResult` details.
Core `ExecutionUsage` provides common immutable counts, and adapter conversion owns provider-specific interpretation.
A `UsageMeter` records observations; Harness allocation admits work and checks observed consumption.

## Python composition

Applications own recovery with ordinary Python and libraries such as Tenacity and `asyncio.timeout`; see [Resilience patterns](patterns.md#resilience-patterns).
`parts.voting` provides repeated sampling and aggregation of answers.

See [Agent composition](agent/index.md), [Agent principles](agent/principles.md), and [host runtime obligations](agent/runtime.md) for the corresponding integration choices.
