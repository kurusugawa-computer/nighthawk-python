# Principles for Nighthawk Agent extensions

Nighthawk turns AI judgment into executable capabilities that can be composed, evaluated, and improved, expanding the work programs can carry out autonomously.
Its Agent extensions let AI agents construct and revise those capabilities during execution by combining Natural judgment with Python computation and actions.
The intended result is useful work and a growing body of reusable capabilities whose behavior and effects can be assessed.

These principles apply the shared [Nighthawk philosophy](../philosophy.md) to Harness and Agent Parts responsibilities and can justify changing their architecture, interfaces, and package structure.
[Architecture](architecture.md) records the current architectural commitments and rationale; [runtime obligations](runtime.md) describe the services supplied by the host application.
Source owns supported behavior and executable contracts.
A principle states a design obligation, not a claim that every desired workflow is already implemented.

## Useful work and accumulated capability

Design for AI agents as the authors and maintainers of programs.
They need to discover functions, understand their intent and contracts, compose them correctly, and revise them using evidence.
Names, signatures, types, descriptions, and useful failure reports are part of the capability surface.
Humans may supply goals, acceptance criteria, and authority policies without writing each program or choosing every decomposition.

Support both completing the present task and making later tasks easier.
Reusable capabilities include judgment criteria, operations, evaluators, and the functions connecting them.
Applications choose what to retain, how to discover it later, and when the evidence warrants reuse.
An individual run need not produce a permanent artifact, and saving source alone does not establish that a useful capability has been acquired.

Judge success against application goals and acceptable effects, including quality, inference cost, latency, and maintenance burden.
More autonomy, deeper recursion, more generated code, and more model calls are not independent objectives.

## Python composition, including runtime authorship

Use ordinary Python functions, values, calls, and libraries as the foundation for composition.
Python defines execution structure; Natural supplies judgment where interpretation of meaning or context is needed.
An agent may author that structure before a task or during its execution.
Runtime authorship does not transfer execution semantics from Python to a conversation protocol.

Let agents choose useful boundaries for investigation, computation, action, and evaluation.
Generated Python may call Natural functions, which may lead to further generated composition through the Harness.
Recursion is one consequence of composition, not a mandatory strategy or a termination argument.
Resource admission and actual completion need explicit owners even when a decomposition appears to make progress.

Composition requires understandable effects, state, ordering, failures, and completion as well as compatible types.
Declared write bindings do not describe every mutation of reachable Python objects or every external effect.
Shared references and partial effects remain deliberate application concerns.
Preserve ordinary Python expressiveness without promising that a type contract proves judgment quality, parallel safety, or rollback.

## Agent Parts and host authority

Agent Parts are reusable Python functions and Natural contracts for deciding and carrying out how work proceeds within host-established authority.
They support different ways to decompose work, discover and construct capabilities, gather evidence, evaluate results, and improve subsequent attempts.
Parts range from an individual judgment or operation to a complete exploration or improvement loop.
No fixed loop, conversation format, or reference application defines all agents.
Composition is the act of selecting and combining parts through ordinary Python; applications may also supply their own parts.
No common base class, mandatory registration, or dedicated invocation protocol is required to make a function an Agent Part.

The host establishes execution authority and supplies the mechanisms that admit operations, account for resources, and record authoritative outcomes.
An agent's freedom to choose a strategy operates within that authority.
Authorship, source provenance, model judgment, and successful execution do not themselves grant permission.
Generated and previously approved compositions must use the same host-owned mechanisms for governed operations.

Treat proposals, permitted execution, observed completion, and approval for future reuse as distinct facts.
An agent may propose broader authority or a changed policy as data; that proposal cannot enact the change from inside the governed execution.

## Judgment and improvement guided by evidence

Use explicit Python rules where they suffice and Natural where judgment is needed.
Revisit that division as requirements, evidence, and model capabilities change.
Replacing a judgment with validated explicit rules can be an improvement.
It is not necessary to remove every Natural block or to turn every task into deterministic code.

Keep assessments connected to the definitions, inputs, results, and effects actually executed.
Type validation establishes value contracts; execution records establish observations; tests, evaluators, and simulations supply bounded evidence about quality.
None alone establishes correctness for every future input.
Unknown outcomes, incomplete observations, and failed evaluations must remain distinguishable from success.

Repeated agreement on observed inputs does not justify replacing a judgment for all inputs.
A replacement needs an understood applicability range and evidence against the application's acceptance criteria.
Fallback, further judgment, or rejection outside that range is an application decision.
Caching prior results and acquiring a reusable rule are different improvements with different evidence requirements.

## Explicit state, evidence, and retained capabilities

Represent operational state through explicitly retained Python bindings and reachable values.
Pass the context needed for a judgment as current typed values or deliberate, bounded projections.
Conversation text is available context when explicitly selected; it is not an implicit source of operational truth.
Larger state may remain behind references and query functions without placing all of it in a model prompt.

Keep operational state, execution evidence, and retained capabilities distinct.
The execution record describes what happened and supports assessment and audit.
It does not become the next execution's working memory merely because it exists.
Retained source, lessons, and procedure descriptions become useful through explicit discovery, evaluation, and invocation.
They do not carry authority merely because they were stored or displayed.

Avoid a universal state root, mandatory transcript, candidate registry, storage backend, or automatic learning lifecycle.
Provide usable compositions and examples for applications that need these facilities, without imposing them on applications that do not.

## Enforcement proportional to effects and authority

Classify a proposed check by the effect it protects.
There are three distinct responsibilities:

1. Perimeter effects, such as egress, destructive operations, and durable write-back, require explicit host mediation and authority appropriate to the effect.
2. Authority, identity, accounting, and authoritative commits require deterministic host decisions because errors would invalidate the execution substrate's account of what happened.
3. Reversible interior operations preserve Python expressiveness and rely primarily on rules and deterministic observation, with detectable violations recorded as evidence.

An interior check may reject an observed violation without establishing a preventive boundary for everything it cannot observe.
Do not turn reversible interior state into a general in-process security sandbox.
When a deployment requires preventive isolation, it must provide an actual external boundary with the guarantees it claims.
Trace, type validation, and an execution process used for scheduling do not substitute for that boundary.

Report the execution regime truthfully.
A refused required guarantee must not silently become a weaker regime.
Likewise, requesting interruption is not observing cessation, and observing a return is not proving that an external effect was durably recorded.

## Independent approval and ordinary software evolution

Keep generation, execution, evaluation, retention, and ratification separate.
Ratification is approval of identified source for a defined scope of future use; it does not grant unrestricted invocation authority.
Successful execution, storage, and tests chosen by the producing agent do not by themselves confer ratified status.

Humans establish the conditions and authority for approval.
Approval may be a human decision or an automated decision by an approval authority independent of the execution producing the candidate.
Independence concerns control over acceptance criteria, required evidence, and the approval decision; a second model invocation alone does not establish it.
The producing execution must not change those conditions, confer approval on itself, or turn its own assessment into an authoritative approval record.

An approval decision must identify the source and relevant dependencies it covers, its scope, the applicable policy, and the evidence supporting the decision.
Evaluation may be automated and may include Natural judgments, with the same obligation to state the limits of its evidence.
The application owns the approval workflow, persistence, versioning, and later reassessment.
Repository modules, ordinary tests, and review processes are useful ways to accumulate capabilities; no proprietary skill lifecycle is required.

## A minimal Core and a usable extension library

Ordinary Python and model libraries can express this work too.
Nighthawk must earn its place by making programs easier to construct and change correctly, their results and effects easier to assess, and their overall complexity lower.
Concise syntax alone is insufficient justification.

Separate three responsibilities within the library:

| Component | Responsibility |
| --- | --- |
| Core | Natural/Python execution semantics, typed bindings, and connection points for composition. |
| Harness | Host-owned execution admission, generated-code dispatch, resource accounting, causal records, and effect mediation. |
| Agent Parts | Reusable Python functions and Natural contracts that decide and carry out how work proceeds within host-established authority. |

These boundaries identify owners of design decisions; they do not prescribe distributions, import paths, or a `parts` package.
Agent Parts use Harness mechanisms for governed operations; the Harness must not depend on one problem-solving strategy.
Core remains usable without the Agent extensions.
Using a model or deterministic Python does not determine a component's responsibility.

Application is the responsibility outside the library that establishes what to achieve, what to permit, and which results and effects to accept.
It owns goals, domain actions, acceptance criteria, policy choices, storage, environments, and improvement and approval processes.
It supplies external runtime services whose guarantees the library cannot establish.
An evaluation procedure may be an Agent Part; its assessment does not confer the Application's authority to approve a candidate.
Assigning a responsibility to the host requires a usable connection point and verified examples, not merely an instruction to implement missing machinery.

Examples are verified reference applications that demonstrate these responsibilities together, rather than a fourth library execution responsibility.
They demonstrate the public composition surface and the services an application must supply; required library mechanisms belong in the library.

Begin with ordinary functions and existing libraries.
Adopt a Core capability when distinct applications share the same execution need, application composition cannot adequately meet it, its meaning and guarantees can be explained clearly, and unused features impose little burden.
Apply the same discipline to shared Harness mechanisms and Agent Parts at their respective boundaries.
Choose module boundaries for capabilities, evaluation, learning, and Session integration when their consumers and reasons for change are clear.
Minimize the total burden on authors, hosts, adapters, and maintainers, including complexity displaced into applications.

## Using these principles to change the design

For a proposed design change, identify the application need, the responsibility that owns it, and the evidence that the change improves useful work or accumulated capability.
Compare it with ordinary Python composition, explain its effects and guarantees, and account for the burden on users who do not need it.
Classify new enforcement using the three responsibilities above before choosing a mechanism.

The current architecture's choice of one interpreter, opaque allocations, particular source lifetimes, and specific dispatch interfaces may be revised when stronger evidence supports a better design consistent with these principles.
A design revision must update its architectural rationale and supported contracts together; changing a principle or specification does not by itself change executable behavior.
