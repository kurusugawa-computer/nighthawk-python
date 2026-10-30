# Philosophy

Nighthawk makes AI judgment an ordinary part of Python programs.
Its purpose is to expand the work programs can carry out autonomously, by composing judgments within tasks and accumulating reusable functions across tasks.
Design for AI agents as the primary authors and maintainers; humans may supply goals and acceptance criteria or judge consequences.

Python composition makes existing operations reusable; explicit contracts make judgments and effects assessable; retained functions let improvements carry into later work.
Nighthawk must earn its place alongside ordinary Python and model libraries through easier construction and revision, assessable results, and lower total complexity.

These principles guide library and application design.
The [specification](specification.md) defines runtime guarantees; [design drivers](design-drivers.md) connect application needs and evidence to decisions.

## Applying the principles

Satisfy the invariants and the application owner's acceptance criteria; follow the defaults unless evidence supports their exceptions.
Among acceptable designs, compare whole-task quality, assessability, maintenance burden, cost, and latency, including the scope of delegation.
Match the strength of evidence and the detail of records to the decision's impact, uncertainty, and reversibility.

## Invariants

These are design obligations, not promises that a model will succeed on every task.

### Python composition is the foundation

Python owns execution structure; Natural supplies judgment within it, from a small interpretation to an extended investigation.
Keep execution interfaces composable through Python calls and existing libraries so computation, domain operations, and verification remain reusable alongside judgment.

### Agents can understand and revise programs

Minimize LLM cognitive load across program authoring, revision, and execution while preserving required contracts.
Public contracts must expose intent, dependencies, result meaning, effects, failure boundaries, and ownership of execution resources and policy.
Make the ordinary usage path discoverable so agents can choose an entry point, compose programs, and locate the contract to revise without depending on development history or human authorship.

### Program text is code

Treat Natural source and other program instructions as code whose authority comes from the host for its intended use.
Authorship, storage, generation, revision, and reuse do not expand that authority.
Keep task data distinct from program instructions; using it as program text requires authorization under the [trust contract](specification.md#3-hard-constraints).

### Acceptance criteria belong to their owner

Revising an implementation does not authorize changing the owner's acceptance criteria or execution policy, wherever represented.
Improving tests and evaluators to implement unchanged requirements is implementation work.
Authority to change the requirements may be delegated in advance; it must come from their owner.

### Guarantees match mechanisms and evidence

Describe guarantees in terms of their mechanisms; limit empirical claims to what evaluation coverage, evaluator accuracy, and uncertainty support.
Make those limits visible where callers rely on them.

### Success requires completed work and acceptable effects

Judge task success by completed work and observed effects against the task's acceptance criteria.
Make required procedural checks enforceable in Python, and assess semantic adequacy with evidence appropriate to the claim.
Connect assessments to what ran and its execution conditions; a valid output or successful step alone does not establish task success.

## Defaults

### Familiar representations over new conventions

Prefer syntax, terminology, and semantics that draw on conventions learned during pretraining so LLMs can use existing knowledge.
Keep the implied meaning consistent with the actual contract.
Use specialized representations when they make the task or contract easier to understand.

### Composition over additional tools and mechanisms

Prefer a small, expressive set of orthogonal tools with distinct responsibilities and predictable composition.
Use existing tools and Python composition when they meet the required contract at acceptable total burden.
Add tools or mechanisms only to address requirements or burdens that existing composition cannot adequately handle.

### Ordinary functions over framework-specific units

Prefer ordinary Python functions to minimize the work of importing, inspecting, testing, and replacing reusable units.
Use a specialized execution interface when function composition cannot express a required contract at acceptable total burden.

### One clear entry point per contract

Prefer one ordinary entry point for each distinct contract, with consistent omission, replacement, and failure semantics.
Use additional entry points when they clarify a difference callers need to understand, such as resource ownership; justify the added vocabulary by its benefit to callers and hosts.

### Explicit rules over judgment where rules suffice

Prefer Python rules when they express required behavior with acceptable maintenance burden.
Use Natural when interpreting meaning or context needs judgment, or maintaining rules would outweigh their benefit.
Revisit the boundary using evaluation evidence as requirements and model capabilities change; model use is not an objective.

### Explicit dependencies over ambient context

Pass essential inputs as parameters so a function's requirements remain visible when it is reused.
Use declared shared context when passing it through intermediate functions adds burden without clarifying their contracts; include it in the receiving function's execution contract.

### Host-selected policy over function-embedded policy

Prefer host-selected execution policy so reusable functions can operate under different models, permissions, and environments.
Embed a check or recovery policy in a function when it is necessary to that function's domain contract.
Reusable libraries can supply host policy, but a function's requirements do not grant it authority to override that policy.

### Reusable functions over one-off solutions

Retain useful functions, judgment criteria, and evaluation evidence so one task makes later tasks easier.
Keep a solution within its task when reuse lacks evidence or costs more to maintain than it benefits.
Applications own retention and revision, checking that reuse preserves contracts and remains authorized.

## Core responsibility

Core means the execution semantics and common contracts required to compose Natural and Python.
Applications, hosts, backends, and reusable libraries own domain operations, policy, orchestration, storage, isolation, and improvement processes.
Responsibilities assigned to hosts must have usable integration boundaries and verified examples.

### Core inclusion criteria

Require a concrete execution need, demonstrated limits of existing composition and libraries, and a reason the requirement generalizes beyond one application's policy.
Require clear guarantees and little burden for applications that do not use the feature.
Evaluate total burden across authors, hosts, adapters, and maintainers, including complexity displaced into applications.
Apply the [evidence criteria](design-drivers.md#from-findings-to-design-decisions) for additions, corrections, and simplifications.

## Accepted tradeoffs

- **Python dependence.** Composition and integration depend on Python's language and ecosystem.
- **Inference cost.** Delegating judgment to a model adds inference cost and latency.
- **Application-owned orchestration.** Applications compose control flow and recovery, including coordination after partial effects.
- **Empirical judgment quality.** Judgment quality requires representative evaluations as tasks, models, and dependencies change.
- **Host integration.** Applications must arrange any isolation, durable state, and operational policy they require.
