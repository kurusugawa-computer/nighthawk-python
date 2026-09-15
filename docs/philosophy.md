# Philosophy

Nighthawk makes natural-language judgment composable with ordinary Python functions.
Developers express intent and judgment criteria in Natural blocks, then combine them with computation, state, actions, constraints, and verification through Python functions and existing libraries.
The aim is to expand the work that programs can carry out autonomously while keeping their results and effects understandable and assessable.

This philosophy guides future design.
The [specification](specification.md) defines supported behavior; [design drivers](design-drivers.md) connect application needs to Core design questions, and the [roadmap](roadmap.md) records exploratory work.

## Function composition as the foundation

Natural functions participate in ordinary Python calls.
A function can retrieve information, interpret it, propose an action, inspect a proposal, simulate consequences, request approval, apply a change, or evaluate the result.
Natural supplies the judgments that depend on meaning and context; Python supplies explicit procedures and integrates the libraries appropriate to the application.

Actions, constraints, and evaluators are all candidates for composition.
For example, a Natural function can assess a proposed change against written rules, return findings as Python values, and let a Python function decide whether to accept it or request revision.
A hook can invoke that composition at the relevant execution boundary.
The application defines the rules and the consequences of a finding.

Start with functions and existing libraries when designing a new capability.
Introduce a Core primitive when that composition exposes a general execution requirement that ordinary application code cannot adequately implement.
Harness engineering is an application of this foundation: assembling intent, capabilities, constraints, environments, and feedback into an executable program.
Business workflows, research, knowledge management, and equipment control can use the same foundation with different domain libraries.
Code generation is optional.

Composition requires understandable behavior as well as compatible types.
Execution order, shared state, effects, failures, and completion determine whether two functions can be combined appropriately.
Retrying an effectful function can duplicate an operation; changing the order of validation and execution can change what is permitted.
These consequences must remain visible to the developer.

## Python structure and delegated judgment

Python owns the control flow and defines the execution structure: calls, state, permitted control results, validation, concurrency, and recovery.
Natural can interpret evidence, choose helpers, investigate a problem, and select an allowed result within that structure.
The host decides where judgment is needed and which conditions surround it.

This supports both predetermined procedures and work whose next action depends on a model's judgment.
A single Natural block may carry out a small classification or delegate a substantial investigation to a coding agent.
Choose boundaries where inputs, authority, acceptance conditions, or recovery policy need to be understood separately.
The application composes judgment, action, observation, and continuation conditions to establish the desired autonomy.

Natural functions also compose with Python concurrency, queues, and service calls.
The host coordinates communication and merging, including any shared mutations.
Filesystem and process isolation belong to the host or backend.

## Humans and agents as authors

Humans and AI agents should be able to read, write, review, and improve Nighthawk programs and their supporting knowledge.
Use meaningful function names, signatures, type annotations, and descriptions so both can understand the available operations and their intent.
Make execution results and failure reasons useful to the next reader or caller.

Natural source and Python code can be maintained alongside trusted knowledge, task procedures, and evaluation criteria.
Applications choose their storage and authoring conventions.
For example, a Markdown knowledge system can organize knowledge and operating rules together while explicitly distinguishing reference material from executable instructions.
Nighthawk's [knowledge-management design driver](design-drivers.md#markdown-knowledge-management) explores this application without prescribing its document format.

## Constraints and feedback

Each mechanism contributes a different kind of assurance:

| Mechanism | Contribution | Limit |
|---|---|---|
| Natural-language instruction | Communicates intent, context, and judgment criteria | Compliance depends on interpretation and execution. |
| Python check or admission hook | Applies a condition at a defined execution boundary | Coverage depends on which operations pass through that boundary. |
| Type validation | Validates the values accepted at the boundary | A valid value can still represent an incorrect judgment. |
| Test or evaluator | Assesses observed behavior against selected criteria | Evidence is limited by the cases and the evaluator's accuracy. |
| Simulator | Provides an environment for trials and consequence assessment | Conclusions depend on how well it represents the relevant real conditions. |

A semantic linter can return findings that a Python hook uses to gate an operation.
Applying that decision consistently does not establish that the semantic judgment is correct.
Applications choose the evaluator, treatment of uncertainty, and acceptance policy.

Feedback is part of the work: propose, inspect or try, observe, then revise, accept, or stop under application-defined conditions.
Prefer observable results and effects to an agent's assertion that a task is complete.
Preserve evidence relating the evaluated proposal to the action actually applied.
Evaluators and simulators can be ordinary functions or external services connected through Python; Core need not implement each evaluation system.

Evaluate the composition against the application's needs.
Deterministic tests verify runtime and host behavior; real-model evaluations assess judgment quality.
Track semantic success and incorrect or duplicate effects before optimizing cost, latency, or call count.
Reassess model-specific guidance and orchestration as capabilities change.
See [Verification](verification.md).

## A minimal, balanced Core

Core provides the execution semantics needed to combine Natural and Python reliably.
Its responsibilities include value transfer, type and control-result validation, state and failure behavior, completion, and the intervention and observation boundaries needed for composition.
Domain-specific actions, policies, and environments belong in application libraries and hosts.

| Layer | Responsibility |
|---|---|
| Nighthawk Core | Natural execution, environments, value transfer, runtime contracts, and execution hooks. |
| Reusable libraries | Composable behaviors such as resilience, evaluation, approval, retrieval, and proposal checks. |
| Application and environment | Domain rules, concrete operations, durable state, permissions, isolation, simulation, and recovery policy. |

These are responsibility boundaries, not a requirement that each layer be a separate distribution.
Nighthawk already includes resilience function transformers; application libraries can supply further compositions.

Evaluate a proposed Core capability against four criteria:

1. It serves distinct applications through a common execution need.
2. Ordinary functions or external libraries cannot adequately provide the required behavior.
3. Its meaning and guarantee can be explained clearly.
4. Applications that do not use it incur little conceptual or configuration burden.

Minimality concerns the total burden on users, adapters, and maintainers as well as the size of Core.
Provide usable connection points and verified examples for responsibilities assigned to the host.
The [workspace information policy](design-drivers.md#workspace-information-policy) keeps application evidence available without making application-specific rules part of Core.

## Execution model

Nighthawk embeds Natural blocks inside ordinary Python functions:

- Read bindings (`<name>`) identify required values and prioritize their presentation; they are not an access allowlist.
- Write bindings (`<:name>`) declare output slots, with annotations defining their value contracts.
- Pydantic validates committed output bindings at step finalization.
- Binding functions let the LLM call Python functions during execution.
- Python syntax and deny frontmatter bound the permitted control results.

For example, Natural can interpret mixed representations of numbers and call a Python helper to calculate their average.
Python performs the arithmetic, and the output annotation validates the returned value as a `float`.
The [Quickstart](quickstart.md) provides a complete example.

Each block starts a fresh model interaction with trusted Natural text, Python values, inherited working state, and scoped configuration.
Model transcripts are not implicitly carried across blocks.
Inherited context reduces argument plumbing but introduces contextual dependencies; prefer explicit parameters for essential inputs and inheritance for intentionally shared task policy.
Shared objects retain their identity and can carry mutations between calls.

Binding functions expose compact Python signatures and execute in the host process.
MCP connects cross-process capabilities; CLI tools connect existing commands.
Choose the interface that fits the operation and its validation needs.
[Coding agent backends](coding-agent-backends.md) provide autonomous execution through the same output boundary, with backend-specific capabilities and visibility.

[Scoped configuration](runtime-configuration.md) follows Python control flow.
Hosts can admit whole function calls, select models through preserving executor wrappers, inspect operations and resolved results, and observe available execution outcomes.
[Resilience functions](patterns.md#resilience-patterns) compose retry, fallback, voting, timeout, budget, and circuit-breaker policies with explicit effect and recovery decisions.

## Trust and safety boundaries

Natural source, imported markdown, and prompt fragments define executable program structure and must be trusted, repository-managed assets.
Pass user input as binding values rather than interpolating it into that structure.
An application's use of Markdown for both knowledge and rules must preserve this distinction.

Typed bindings validate name writeback; they do not sandbox arbitrary instructions or make a block transactional.
Helper calls, object mutations, validators, and external operations can have effects before finalization.
The host owns approval, durable records, compensation, and retry decisions.

Scoped hooks are configurable and can be cleared; backend-internal operations may not be visible through Nighthawk's boundaries.
Applications requiring non-bypassable controls must establish them through their host and execution environment.
Core does not promise comprehensive mediation of arbitrary agent behavior.

Core waits for its owned work, including cancellation cleanup, before reporting completion.
That wait may be indefinite; it neither rolls back effects nor joins detached application work.
Coding-agent permissions and operating-system isolation govern what the backend may do during execution.

## Tradeoffs

- **Python commitment.** Functions, annotations, scopes, and composition use Python and its ecosystem; Nighthawk offers no language-neutral orchestration protocol.
- **Inference cost.** Each production Natural block invocation delegates judgment to an LLM or agent execution; keep explicit computation in Python.
- **Composition discipline.** Shared state, ordering, retries, partial effects, and recovery require deliberate application design.
- **Empirical reliability.** Runtime validation establishes only its stated contracts; judgment quality needs representative model evaluations.
- **Backend differences.** Common output contracts do not imply identical permissions, internal request visibility, or capabilities.

## Why evaluate every time

In the current execution model, Natural judgments run for each invocation using the particular input and context.
Natural prose is interpreted work rather than generated deterministic Python source.
When explicit rules determine the answer, implement those rules directly in Python.

Per-invocation evaluation lets judgment respond to current data and environment.
Its value and cost belong in the application's evaluations; the [specification](specification.md) defines the supported execution behavior.
