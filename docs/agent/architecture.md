# Agent architecture

Architectural commitments and design rationale for `nighthawk.harness` that have no natural owner in source.
The [principles](principles.md) define the purpose and design criteria above this architecture and may justify replacing its decisions.
Behavior, contracts, types, and validation are owned by the source under `src/nighthawk/core/`, `src/nighthawk/harness/`, and `src/nighthawk/parts/` and its docstrings;
this document does not restate them.
External obligations of the host application are in [runtime obligations](runtime.md).

## Identity

Harness and Agent Parts build on Core, with separate ownership described in [Architecture](../architecture.md).
Their purpose is to support agents that construct, execute, assess, and improve capabilities using Natural and Python.
They have no mandatory agent loop, framework hook surface, or application boundary.
The execution substrate consists of grants, code proposals, host-owned dispatch, typed commits, the run ledger, and host runtime services.
Recursive generation and invocation are supported compositions over this substrate.
Every fixed loop, including the exploration loop in `nighthawk.parts.exploration`, is a scaffold composition and creates no authority outside it.

## Responsibility boundaries

The [package ownership table](../architecture.md#package-ownership) defines the library boundaries.
Session supplies approved callable associations as run-entry definitions instead of writing ledger events or editing Harness caches.
The Harness retains exact run-entry callable identity so Session can admit shared Natural parts without trusting module names or accepting self-registration during a run.

## Execution roles

Deterministic Python executes in exactly one of three roles: host-control-plane mechanism, ratified interior execution, or generated execution.
The role is an execution property established by the host.
Authorship, implementation language, and source provenance class confer no authority.
Only host-control-plane mechanisms validate, account, record, admit, and commit.
Ratified interior execution (scaffold control flow and the deterministic Python of ratified Natural functions) and generated execution orchestrate and request operations through those mechanisms and act only within their grants.
Natural executions produce semantic judgments only and hold no authority to commit governance state.

Authority is admitted once, at run entry, as the authority ceiling: the run-entry grant and the composition perimeter policy.
After run entry no interior execution, mediation seam, or host seam widens it.
Nested scopes and invocations receive no more authority than their submitting execution holds, and returning from a narrower scope restores the enclosing authority; attenuation is not run-wide revocation.
A run that needs more authority or allocation is a new run.

## Typed semantic boundary

Every LLM-derived output that crosses from Natural execution into deterministic execution crosses exactly one typed semantic boundary:
a value satisfying a declared write-binding type or a request satisfying the request type of a granted capability.
Untyped model output is never accepted as authoritative state or executable source.
New generated source reaches dispatch through exactly those two boundaries: an inert `CodeProposal` returned through a write binding and submitted by deterministic Python, or a typed request to the granted `execute_code` capability.
Both enter the same host-owned dispatch, and no interface accepts only a natural-language subproblem and thereby initiates recursive model execution.

Within an agent run, a `raise` outcome is classified as a failed Natural execution rather than as the requested exception type; the raised exception, including a re-raised original from a failed operation, is its cause.
The rationale is one stable public failure class for every Natural execution failure under host mediation, with the requested type preserved as data;
"ordinary Python semantics" here means ordinary `try`/`except` handling and propagation of that class.
The exact contract is owned by `NaturalExecutionError`.

Within an agent run, the core `return` outcome is not offered to the executor and is a failed Natural execution if an executor emits it anyway.
A model-selected return would skip the remaining deterministic Python of the containing Natural function and would be neither a declared write binding nor a capability request.
A Natural block inside an agent run returns its judgment through declared write bindings; the deterministic Python of the containing Natural function decides what to return.
A `decide` block is the typed form of such a judgment: it permits only `pass` and commits exactly one declared write binding.
Its decision model receives the explicitly referenced evidence and no action tools, and its requests are admitted and accounted like those of an `execute` block.
The write set is validated once, by the core; the host operates two core seams around that validation.
The host's step-commit inspector admits the validated candidate before assignment and refuses an execution terminated meanwhile, so nothing is assigned;
the host's step lifecycle receives the terminal record after assignment and records exactly one outcome event per Natural execution, including failures of core stages the executor seam never sees and interruptions,
translating every failure into the library's typed exception chained to the original.
An execution the core finishes without delivering its terminal record is a trace anomaly with a failed outcome at the next host seam.
The same reasoning excludes any other core oversight hook or lifecycle and the core tools and Pydantic AI capabilities declared outside the host projection from agent runs:
a foreign or cleared inspector or lifecycle bypasses the host seams, and a tool or capability visible without a grant is ambient authority, so the host records each as a trace anomaly and fails the execution.

## Graduated enforcement

The [principles' three enforcement responsibilities](principles.md#enforcement-proportional-to-effects-and-authority) govern the checks below.
This architecture implements them as follows.

- Perimeter effects (egress, destructive operations, durable write-back) must use typed, host-mediated capabilities within the effective perimeter authority.
  Host gateways prevent unauthorized capability admission and record perimeter attempts before execution.
  In-process execution adds limited CPython audit checks that reject observed direct effects and record trace anomalies; these checks are detective-grade and do not establish confinement.
  Optional external confinement supplies preventive isolation beyond Python when configured, and its confirmation is required at every dispatch in that configuration.
  Absence of that service is an explicit execution regime, never a fabricated confirmation or a fallback from a refused confirmation.
- Authority, identity, and accounting are typed host decisions made at host seams before the effect:
  grants only attenuate, generated code runs only inside its dispatch, every Natural execution and dispatch node has exactly one terminal event, committed values enter the ledger as immutable snapshots, and consumption is admitted before it happens wherever the host can observe it.
  These decisions are hard because being wrong about them corrupts the substrate's model of what happened, not because they defend against an adversary.
- Reversible interior effects, including shared-namespace mutation and the evaluation of model-authored expressions inside a Natural execution, are governed by rules plus deterministic trace (detective control).
  A detected inconsistency between observed state and ledger-derivable state is recorded as a trace anomaly with causal parentage;
  trace anomalies are evidence and never establish preventive enforcement or perimeter confinement.
  A cheap deterministic check may reject a detectable violation before it runs, but the check is detective-grade: no perimeter guarantee depends on it, and a violation it misses is a trace anomaly, not a breach.

Session operator controls establish host ownership and do not become Agent capabilities through aliases or retained data.
Local organization busy checks and bounded automatic previews are usability contracts for reversible state; they do not harden the Python object graph or establish perimeter confinement.

Before adding a check, classify the effect it guards into one of these tiers;
a check that would harden reversible interior effects preventively is out of scope by design, because interior expressiveness is preserved while host authority decisions remain hard.

The rule against raw dynamic execution (`exec`, `eval`, `compile`, dynamic import of constructed text, or running an interpreter on constructed text through command capabilities for the run's own logic) is the detective check of the third tier.
Dispatch validation rejects detectable violations in generated source deterministically before execution.
The host applies the same rule to the model-authored expressions the core executor evaluates inside a Natural execution:
it inspects each expression-bearing operation, rejects detectable raw dynamic execution before evaluation, and records the attempt as a trace anomaly.
A recoverable operation refusal gives the model its reason and correction guidance, and a failed operation gives it the exception class and message, so it can choose a different operation within the same Natural step or raise the original exception.
Recovery preserves prior effects, the effective grant, resource accounting, and the single terminal outcome; it never replays the block or clears a host stop.
Explicit step aborts, rejected perimeter effects, inspection failures, and notification failures remain terminal.
This distinction does not change the detective tier or establish perimeter confinement.
No other filtering of those expressions exists.
An operation evaluated under a host gateway allowance is instead an authority error of the second tier, independent of its expression:
it would act with host infrastructure or capability authority, so it fails the Natural execution as a mediation bypass.
Running task artifacts such as a project's own tests through command capabilities is not a violation.

## Trust model

Nighthawk executes Python, so it trusts by provenance and role rather than by inspecting each expression.
Ratified source is trusted through repository governance or explicit operator definition submission for one Session.
Generated source is governed through dispatch validation, limited effect observation, and trace, with optional external confinement.
Model output is never trusted as state or source:
within an agent run it becomes authoritative only as a typed value crossing one of the two semantic boundaries described above (a write-binding value or a capability request),
and model-authored expressions run only inside the executor's evaluation, within the frame the block's author exposed.
Core control outcomes do not introduce a third channel for accepting model-authored state or source into the Harness.
The library therefore does not attempt an in-process sandbox, an expression allow-list beyond the raw-dynamic-execution rule, or any claim of adversarial safety for interior effects:
in-process Python cannot be made safe by filtering, and filtering would remove the DSL's expressiveness.
Native extensions, unaudited descriptor operations, shared memory, and execution without propagated context remain accepted limitations of the ordinary interpreter.
Deployments needing stronger guarantees must provide an actual external boundary and identify it truthfully.

## Source provenance and ratification

Every run-visible executable source item is either ratified source or generated source, and ratification is the only transition between them.
Generation never confers ratified status, and untrusted data never reclassifies generated source.
A materialized generated definition is run-scoped: it has a stable identity for the remainder of its run, may be retained and re-invoked through ordinary Python references, and never carries invocation authority beyond that run.
Applications may retain an exact generated definition as a `RetainedFunction`, independently of Session, without retaining its materialized function for invocation or its authority.
Later materializations receive fresh run-local identities and link only the selected export to its originally observed definition identity.
Such a callable remains generated source and never becomes approved by retention or successful evaluation.
Its identity survives variable rebinding.
Session wraps the shared retained callable to connect operator Python-cell calls to fresh runs and interior calls to the existing run.
Closing a Session prevents calls through its `SessionFunction` adapters.
An explicitly selected `retained_function` remains reusable by another host-established run without carrying the Session's authority or approval.

Ratification occurs outside agent runs through an independent approval authority under human-established conditions, as required by the [approval principle](principles.md#independent-approval-and-ordinary-software-evolution).
Repository governance may record a human decision or an independent automated approval decision to land identified generated source as an ordinary repository module, using selected run evidence and evaluation results.
The candidate-producing execution cannot control the applicable acceptance criteria or make the approval decision authoritative.
Approval identifies the exact source and relevant dependencies, the policy and evidence used, and the scope of future use.
Changes outside that approved identity or scope require a new decision under the application's policy.

The library consumes approved repository source and explicit Session approval evidence; it does not implement an automated evaluation or repository-approval pipeline.
An external approval workflow may use agents for assessment, but its authoritative approval action remains outside agent runs.
Approved repository definitions enter the library through ordinary imports and, where Session requires callable correspondence, trusted imported-function registration.
The Session route treats explicit operator submission of Python definitions as approval of that exact submitted source for that Session alone.
Approval is separate from successful definition materialization and from later invocation authority; Session disposal invalidates its admission evidence without rewriting escaped ordinary Python objects.
Callable correspondence is established only by observed definition materialization, a core-owned transformation of that exact definition, or trusted imported-function registration asserting reviewed source and callable identity.
Names, source lookup, and writable callable metadata alone do not transfer approval.
Natural-language input, model output, historical text, and source storage or export never constitute operator submission.
Embedding applications must not submit those inputs as operator Python to acquire approval, or interpolate them into Natural source and include preprocessing.
An independent approval workflow must establish its decision before source is admitted as approved; relabelling candidate data as operator input is not such a workflow.
Run-interior paths cannot self-ratify or mutate policy artifacts.
Callers may retain candidate source and selected ledger evidence as inert Python values and export them through a granted durable write-back capability;
doing so neither ratifies the source nor keeps a live callable valid across runs.
There is no mandatory candidate registry, automatic export, or whole-ledger persistence.

## Untrusted data

Run-entry task input and environment content returned through capabilities, together with values derived from them, are untrusted data.
Untrusted data reaches Natural execution only through declared read bindings or typed capability results,
and reaches generated execution only through typed call arguments, explicitly granted references, or typed capability results.
Neither host mechanisms nor ratified interior execution splice untrusted text into Natural source text, markdown preprocessing, or generated source text.
Natural DSL sources and included markdown remain trusted, repository-managed assets.

## Operational state and history

Operational state is a role played by explicitly retained ordinary Python bindings and reachable values.
There is no universal state root, state base class, patch language, or conversation-transcript substrate.
Nothing from earlier execution is an implicit input to a later Natural execution:
cross-execution context lives only as current typed Python values or as bounded typed projections of them.
The run ledger is the authoritative causal and audit record, not an operational-memory substrate;
history projections are deterministic filters over it, and any semantic derivation of history is a separate Natural block whose typed output is committed before projection.
Prompt-facing projections and capability-result previews are bounded by the platform's prompt bounds; an incomplete projection is explicitly identified as incomplete.

## Scaffold obligations

Scaffold compositions are non-privileged.
They may restrict the substrate but cannot bypass its mediation seams.
Two obligations are not mechanically enforceable by the library and rest on the composition:

- A scaffold must not replace the step executor inside a run (for example through the core `scope(step_executor=...)`), because Natural executions would then bypass host mediation.
- Before returning or propagating an exception, a scaffold must ensure that every interior branch it started has terminated.
  Branches still active at the run-end seam are a trace anomaly; the host never accepts the returned value and relies on the platform termination mechanism to establish cessation before the run can end at all.

Concurrency, scheduling, and isolation of interior branches are platform or scaffold choices;
the substrate guarantees only the authoritative order of host-owned commits.
Disjoint write-binding names do not establish parallel safety: aliases, read/write dependencies, and external effects may still conflict.
Shared interpreter identity does not imply that every name is exposed to every Natural execution.

## Design rationale

- One mediation path.
  A single host-owned dispatch keeps validation, resource accounting, provenance, and grants uniform without making Python call sites carry control-plane ceremony.
  Rejected: prose-only subagent invocation, a second execution mechanism for reusable generated functions, and a third recursion entry outside the two typed semantic boundaries.
- Two execution intents.
  Definition-only and definition-and-invocation are the closed intent set so dispatch can decide intent-source mismatch deterministically;
  the combined form composes materialization with the same mediated invocation rather than introducing independent semantics,
  so it produces two dispatch nodes under the submitting execution: the materialization node and the ordinary invocation node of the entry export.
- Generated code runs only inside its dispatch.
  Function exports are rebound to their host wrappers inside the generated namespace, including the namespace copies generated Natural functions capture,
  so a sibling call or a returned sibling enters dispatch like any other invocation.
  Every generated function body, exception handler, `finally` block, lambda, and generator expression is compiled with a host guard bound under a reserved name,
  and every generator, async generator, and coroutine function is compiled to return a host proxy whose `send`, `throw`, and `close` (and asynchronous counterparts) consult the guard,
  so a private helper, closure, method, generator, or coroutine that escapes its dispatch cannot start, resume, or run its handlers outside a live generated execution of its own source,
  whether through ordinary calls, `next`, `throw`, `close`, garbage collection, or a copied context replayed after the dispatch recorded its outcome.
  A dispatch node admits generated code only while it is nonterminal; the run root admits ratified interior execution only while the scaffold composition executes.
  Rejected: scanning results for leaked functions, because any container or object can carry one; trusting the call site to hold only wrappers, because a raw function retained across run end would execute generated source without a run;
  guarding resumption points inside the body, because exceptional resumption bypasses expression-level guards.
  Generated source may not name the reserved guard bindings, use the introspection that could reach them, define generator lambdas, or define generator or coroutine methods; a guard rebound from outside is a trace anomaly and is restored at the next invocation.
- Ledger values are snapshots.
  Validated generated-function and capability invocation arguments, committed write sets, dispatch results, capability results, and run results enter the ledger as immutable snapshots:
  frozen containers, typed frozen records whose every held value is itself immutable, definition identities for generated callable references, and descriptors for other callables.
  Snapshotting is total: a value that cannot be represented is recorded as a descriptor naming the failure, so the terminal outcome event is always recorded.
  Invocation inputs are captured after validation and before entering the body; a later mutation or failure cannot rewrite the inputs used for assessment.
  Rejected: recording live references, because a mutation after commit would rewrite the audit record; letting a representation failure skip the outcome event, because a missing terminal event is a worse audit defect than a placeholder value.
- Dependencies are declared by name and type.
  A dependency declaration carries the capability signature text or the reference type the generated source was written against,
  so a grant whose entry differs in kind or type produces a typed rejection before the body runs instead of a failure inside generated execution.
- No universal dispatch handle, result type, suspension protocol, or host scheduler.
  Each invoking interface's call contract represents completion and failure; asynchronous composition is an ordinary Python choice subject to descendant termination.
- Definition identity is not a source-level name.
  Independent materializations may share export names and source text; caller-local bindings may be rebound freely.
  Rejected: run-global name reservation, mandatory idempotent materialization, and object-attached provenance metadata as authority.
- Atomicity covers declared write sets, not reachable object graphs, and already mediated effects are never rolled back.
- Opaque allocation.
  Agent-visible countdowns, caller budgets, composition-defined accounting units, and mid-run allocation changes are rejected because they expose control-plane arithmetic without semantic value or require a mid-run governance seam that no transition can reach.
  Opacity is the current allocation design, not a universal consequence of host ownership; changing it requires revisiting the runtime contract and evidence for exposing resource information.
- One interpreter for live execution state.
  Python, Natural, and generated callables share ordinary references, object identity, the host, and the ledger in their owning Session or application interpreter.
  No per-cell, per-run, or per-dispatch process or subinterpreter is introduced.
  Embedded Session and Runner execution stays in the caller process and event loop without taking ownership of signals or streams.
- Honest confinement selection.
  Requiring an external sandbox for ordinary Python execution was rejected because the library deliberately accepts the interpreter's structural limitations.
  Configured external confinement must confirm every dispatch regardless of input provenance; a refused confirmation never falls back to ordinary execution.
- Perimeter audit durability covers only attempt and completion events, so an authoritative post-run account of irreversible effects exists without imposing unrelated persistence.
  A run permitting perimeter effects requires explicit audit-store selection. This is a perimeter admission requirement, not a preventive restriction on reversible Python state.
  Rejected: silently supplying process memory for a durable-audit obligation.
  Storage implementation and deployment guarantees remain application-owned.
- No completion gate or run-outcome proposal type.
  Re-entry after a rejected completion would require a suspension protocol; ordinary return and raise yield at most one terminal candidate, and domain result checks are ordinary scaffold Python.
- Capability families are optional compositions over grants with no normative catalog, profile vocabulary, or permission tiers;
  the run-entry grant with the composition perimeter policy is the ceiling and reusable configuration is ordinary Python.
- Generated export kinds are a scope decision, not an architectural requirement.
  Functions (synchronous and asynchronous, including Natural functions) and values are supported because their call contracts are ordinary Python call protocols the host can mediate on every entry.
  Classes are not exported because construction, method mediation, and use as types each need their own design;
  classes may still be defined as private helpers inside generated source.
  The combined definition-and-invocation intent invokes synchronous entries only, because `dispatch()` is a synchronous interface.
