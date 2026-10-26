# Philosophy

Nighthawk makes AI judgment an ordinary part of Python programs.
The aim is to expand the work programs can carry out autonomously: within a task by composing judgments, and across tasks by accumulating reusable functions.
Design for AI agents as the primary authors and maintainers of these programs; humans may supply goals and acceptance criteria or judge consequences.

Ordinary Python and model libraries can also express this work.
Nighthawk must earn its place by making such programs easier to construct and change correctly, their results and effects easier to assess, and their overall complexity lower.
[Design drivers](design-drivers.md#evaluating-nighthawks-value) define how to compare it with those alternatives.

This page connects Nighthawk's purpose to its foundational design choices and provides principles for evaluating tradeoffs and proposed changes.
It guides people and AI agents designing Nighthawk and programs built with it; application owners set their own goals, acceptance criteria, and acceptable risks.
[Core responsibility](#core-responsibility) describes the obligations of the library itself.
The [specification](specification.md) defines runtime behavior and guarantees, and [design drivers](design-drivers.md) connect application evidence to design decisions.

## Applying the principles

Evaluate proposed changes by whether they help agents construct, reuse, assess, and improve programs.
Choose only designs that satisfy the invariants and application acceptance criteria, using the defaults unless evidence supports their stated exceptions.
Compare choices, including the scope of delegation, by whole-task quality, assessability, maintenance burden, cost, and latency.
Match the strength of evidence and the detail of records to the decision's impact, uncertainty, and reversibility.

## Invariants

### Python composition is the foundation

Python owns execution structure; Natural supplies judgment within it, from a small interpretation to an extended investigation.
Keep every execution interface composable through Python calls and existing libraries, including when an application uses an external orchestration framework.
This makes existing computation, domain operations, and verification reusable alongside model judgment.

### Agents can understand and revise programs

Design for AI agents to discover, compose, assess, and revise programs without relying on development history.
Make intent, dependencies, result meaning, effects, failure behavior, and execution requirements understandable through function contracts.
Human authorship must not be a prerequisite for program construction or maintenance.

### Program text is code

Treat Natural source and other program instructions as code whose authority comes from the host for its intended use.
Authorship, storage, generation, revision, and reuse do not expand that authority.
Keep task data distinct from program instructions; using it as program text requires authorization under the [trust contract](specification.md#3-hard-constraints).

### Acceptance criteria belong to their owner

Revising an implementation does not itself authorize changing the owner's acceptance criteria or execution policy, wherever those requirements are represented.
Improving tests and evaluators to implement unchanged requirements is implementation work.
Authority to change the requirements may be delegated in advance; it must come from their owner.

### Guarantees match mechanisms and evidence

Describe guarantees in terms of what their mechanisms establish, and limit empirical claims to what evaluation coverage, evaluator accuracy, and uncertainty support.
Make those limits visible where callers rely on them.
For example, validating a result's type does not establish the correctness of its meaning.

### Success requires completed work and acceptable effects

Judge task success by completed work and observed effects against the task's acceptance criteria.
Make required procedural checks enforceable in Python, and assess semantic adequacy with evidence appropriate to the claim.
A valid output or successful step alone does not establish task success.
Keep enough evidence to connect an assessment to what ran and the relevant execution conditions.

## Defaults

### Ordinary functions over framework-specific units

Prefer ordinary Python functions as units of composition and reuse, minimizing the additional work needed to import, inspect, test, and replace them.
Use a specialized execution interface when ordinary function composition cannot express a required contract at an acceptable total burden.

### Explicit rules over judgment where rules suffice

Prefer Python rules when they express the required behavior with acceptable maintenance burden, and use Natural for interpretation of meaning or context.
Keep judgment in Natural when rules inadequately capture the task or their maintenance burden outweighs their benefit.
Revisit the boundary using evaluation evidence as requirements and model capabilities change; model use is not an objective.

### Explicit dependencies over ambient context

Pass essential inputs as parameters so a function's requirements remain visible when it is reused.
Use explicitly declared shared context when dependencies span a task and passing them through intermediate functions adds burden without clarifying their contracts.
Include that context in the receiving function's execution contract.

### Host-selected policy over function-embedded policy

Prefer host-selected execution policy so reusable functions can operate under different models, permissions, and environments.
Embed a check or recovery policy in a function when it is necessary to that function's domain contract.
Reusable libraries can supply host policy, but a function's requirements do not grant it authority to override that policy.

### Reusable functions over one-off solutions

Prefer retaining useful functions, judgment criteria, and evaluation evidence so work on one task makes later tasks easier.
Keep a solution within its task when reuse lacks supporting evidence or would impose more maintenance burden than benefit.
Applications own retention and revision, including checks that reuse preserves required contracts and remains authorized.

## Core responsibility

Core supplies the execution semantics and usable connection points needed to compose Natural and Python reliably.
Applications, hosts, backends, and reusable libraries own domain operations, policy, orchestration, storage, isolation, and improvement processes.
Responsibilities assigned to hosts must have usable integration boundaries and verified examples.
Reduce total burden through better composition, documentation, libraries, or the simplification and removal of features.

### Core inclusion criteria

Add a Core feature only when distinct applications share an execution need that existing composition and reusable libraries cannot meet correctly with acceptable total burden.
Require clear guarantees and little conceptual or configuration burden for applications that do not use the feature.
Evaluate total burden across authors, hosts, adapters, and maintainers, including duplicated execution machinery and complexity displaced into applications.

## Accepted tradeoffs

Nighthawk accepts these costs of its design choices and seeks to reduce them while preserving the invariants.

- **Python dependence.** Composition and integration depend on Python's language and ecosystem.
- **Inference cost.** Delegating judgment to a model adds inference cost and latency.
- **Application-owned orchestration.** Applications compose control flow and recovery, including coordination after partial effects.
- **Empirical judgment quality.** Judgment quality requires representative evaluations as tasks, models, and dependencies change.
- **Host integration.** Applications must arrange any isolation, durable state, and operational policy they require.
