# Runtime service obligations

The host application operates the Harness and supplies the external guarantees the library cannot establish itself.
This document uses "platform" for those host-side runtime services, not for an additional Agent strategy or a required product.
The [principles](principles.md) define the responsibility boundaries; [architecture](architecture.md) records the current design consuming these services.
The Python contract is `nighthawk.agent.runtime.Runner`, with service protocols owned by `runtime.py`;
this document records the obligations behind that contract, which the library consumes but cannot verify.
Library-side validation or trace never substitutes for them.

## Application ownership

The application chooses domain actions, acceptance criteria, execution strategies, storage, and improvement workflows.
Harness service implementations supply admission, accounting, evidence durability, and any configured confinement or termination guarantees.
Selecting or combining Agent Parts does not change those service obligations or confer additional authority.
An evaluation performed by an Agent Part supplies an assessment; the Application retains ownership of acceptance criteria and independent approval authority.
The same obligations apply whether a composition is called directly, through a Session, or from an example application.

Source approval belongs to application governance outside agent runs.
Under the [independent approval principle](principles.md#independent-approval-and-ordinary-software-evolution), humans establish the conditions and an independent human or automated authority may make the approval decision.
The application binds the decision to identified source and relevant dependencies, its scope, policy, and supporting evidence before presenting that source as approved.
Runner supplies no automatic approval workflow, and its audit store does not approve the source whose effects it records.

## Perimeter policy

The platform establishes the platform perimeter policy for a run at run entry from platform policy.
A scaffold composition may only narrow it; the effective policy is the intersection and remains fixed for the run.
The permitted classes are the irreversible effect classes: egress, destructive operations, and durable write-back.
A capability family that can cause any of these classes must declare them in its capability footprints, because the effective policy is enforced against declared footprints.

## Opaque resource allocation

The platform fixes one resource allocation per run at run entry, covering at least recursion depth, executor step count, and cost.
The allocation is opaque to semantic execution and scaffold compositions:
they neither declare nor manage it, and numeric allocation state never enters prompt-facing context.
The host presents every resource-consuming request (dispatch admission, Natural execution admission, executor step admission) for admission and reports metered consumption afterwards;
the allocation may partition its envelope across subtrees of the attribution tree, but partitioning is not exposed.
Executor steps are presented one by one, and how the host observes them is decided at run entry (`nighthawk.agent.runtime.ExecutorStepObservation`).
When the step executor is the core `AgentStepExecutor` over a Pydantic AI agent, the host installs a Pydantic AI capability for the Natural execution through the core scope, and the core passes it to every agent run:
the step is presented before each model request, streamed or not, and the response usage is reported to the allocation immediately, so step and token exhaustion prevent the next request.
A custom executor that declares `admits_executor_steps = True` obtains `nighthawk.agent.current_executor_step_admission()` and wraps each provider request in `with admission.request():`, then calls `admission.observe(usage)`.
The context admits a step and executes provider communication as host infrastructure until exit; tool evaluation and model-authored expressions execute outside it.
An operation evaluated inside it fails the Natural execution as a mediation bypass before evaluation.
`admission.admit()` alone provides accounting admission without effect permission.
These two modes are the configurations in which the library enforces the per-step bound itself.
Any other executor reports usage only after its Natural execution; the host admits such a run only when the platform sets `Runner.accepts_aggregate_executor_accounting`,
which records that the platform enforces the per-step bound inside its executor or provider; the run-entry event records the observation mode so the regime of every run is auditable.
In that mode the host presents the steps it learned of afterwards, and an execution that consumed beyond the allocation ends as a typed `allocation_exhausted` failure whose write set is not committed;
the library does not claim per-step prevention for it.
Usage is attributed per Natural execution through a usage meter the host installs for that execution alone (`nighthawk.scope(usage_meter=...)`), so concurrent or nested executions never account each other's requests.
Exhaustion is a typed, ledger-recorded failure of the affected request and creates no continuation or resumption contract;
the allocation is never extended or replaced during a run.

## In-process execution and optional confinement

The default Runner executes ratified and generated Python in the owning interpreter, preserving ordinary object identity and references.
`perimeter_confinement=None` means no external confinement is requested, and each dispatch records `confinement_identity=None`.
No generated execution subprocess, subinterpreter, fake confirmation, or serialized locals mirror is introduced.

A process-wide CPython audit hook observes only contexts carrying an active host.
Selected audited writes, deletion, network operations, and process creation outside host gateways are rejected and recorded as `unmediated_perimeter_effect` trace anomalies.
Read-only imports remain ordinary Python; unprivileged import cache writes are refused with the standard cache-unavailable behavior, without authorizing the imported module's body.
The hook does not modify global interpreter cache settings, signals, streams, or scheduling.
Native extensions, unaudited descriptor operations, shared memory mutation, and work outside the propagated context are not confined.
These limitations are accepted by the in-process regime, and trace is not evidence that an unobserved effect was prevented.

Capability grants, types, declared semantic footprints, perimeter policy, and durable attempt admission are checked before entering the capability body.
An admitted perimeter capability owns its trusted implementation, including temporary-file cleanup; Python system-call classes do not redefine its semantic footprint.
Configured host services and admitted model requests execute as host infrastructure, including provider client internals such as platform probing.
These dynamic allowances expire on return, failure, or cancellation, including in copied task contexts.
Every Natural execution and other nested interior execution starts without the gateway allowance, so a Natural function called from a perimeter capability evaluates its operations without that capability's effects.
An operation evaluated while a gateway allowance is active records an `operation_under_gateway_allowance` trace anomaly and fails its Natural execution as a mediation bypass.
A nonperimeter capability receives no direct-effect allowance.

When an external confinement service is explicitly configured, the platform must confirm every dispatch.
A missing confirmation rejects dispatch before generated execution begins and records no admitted dispatch event; the library never silently falls back to ordinary execution.
A confirmation asserts a real preventive boundary in the actual execution environment, with permitted effects exposed only through host-mediated capabilities.
The library neither implements nor infers that external boundary, and recursive dispatch cannot weaken it.

## Durable perimeter audit

A run whose effective policy permits perimeter effects must have an explicitly configured `Runner.perimeter_audit_store`.
Omitting it rejects the run before admission. Selecting `InMemoryPerimeterAuditStore` explicitly declares an experimental configuration that does not satisfy durability.
The application-owned [SQLite example](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/examples/durable_audit.py) commits each record before returning and demonstrates reopening observations after process exit.
Its guarantees depend on the selected local filesystem and storage honoring SQLite synchronization; no library check certifies those deployment guarantees.

Before a capability effect whose footprint contains a perimeter class is attempted, the host hands a perimeter-effect attempt record to the platform store, and only after it returns does the host attempt the effect.
The store must persist the record before returning; if it raises, the effect is not attempted.
The host then records a completion record with the observed status.
Attempt and completion records must survive run end and host-process end and remain available for audit;
an attempt without a matching completion has authoritatively indeterminate completion status.
This durability obligation covers only the perimeter-event subset of the run ledger.
Durability of other ledger events is platform policy.

## Prompt bounds

Every prompt-facing state projection, capability-result preview, and ephemeral executor history has a platform-enforced bound independent of elapsed run horizon.
The platform supplies the step executor and its context limits;
Natural executions inside a run pass through the host's wrapping executor, and the delegate executor's rendering bounds are the enforced prompt bounds.
Python state may remain larger behind stable references and bounded typed query capabilities.

## Branch termination

Ordinary in-process interruption is cooperative and Python signal delivery does not guarantee interruption of native code.
The default termination service reports no forced cessation; closing admission or requesting cancellation never substitutes for observed completion.
A parent execution may request forced termination of a descendant dispatch branch through the host capability when the platform supplies an actual mechanism.
The mechanism must stop the targeted branches without their cooperation, including when they are locked or stuck, and report cessation only when the branches can no longer execute or submit new operations.
The host reports completion to the parent only after cessation is established and the affected Natural-execution and dispatch outcomes are recorded.
A request or acknowledgement alone never establishes termination.
Forced termination does not roll back declared effects, restore shared objects or locks, or guarantee safe continuation of the parent;
already attempted perimeter effects retain their completion status.
The same mechanism serves host-directed run termination through `RunHandle.terminate` and the termination of branches a scaffold left active at run end.
For host-directed termination the affected set includes the run root while the scaffold composition is still executing, because its ratified interior execution is a branch that must cease like any other;
the mechanism must be able to stop that execution as well.
When it establishes cessation, the host records the affected outcomes and the run end immediately and the pending run call raises the typed run failure.
An asynchronous composition runs on the awaiting caller's loop, retaining the same host context across suspension, and its terminal value is the awaited result.
Cooperative cancellation unwinds the body and records its terminal decision before propagating the caller's cancellation.
When the mechanism does not establish cessation, the run does not reach its run-end seam:
the caller receives a typed error or a `False` completion, the run ledger records no run end, the host refuses further interior operations at its mediation seams, and the affected dispatch nodes remain nonterminal.
Closed interior admission alone is not completion.
After the body unwinds, the owner may retain cleanup responsibility and retry the private finalization path once branch cessation can be established.
That path never re-enters the body, renews allocation, or reopens admission; it preserves the original failure class and records exactly one terminal event.
Branch owners must join arbitrary Python tasks and threads they create; the library does not discover or forcibly stop unregistered work.

Session background work uses explicit trusted owner registration rather than task/thread discovery.
Owner observations distinguish interruption requests from confirmed cessation; failed observation remains unknown and blocks operator exit.
Operator exit checks blockers before changing admission or cancelling work, and a refusal creates no pending shutdown.
Accepted shutdown prevents new registration and finishes after the owning foreground cell unwinds and managed cessation is established.
A terminal transport loss does not release the surviving execution owner's cleanup obligations, and a worker crash is never reported as successful cessation.
The embeddable library retains caller-owned event loops and never exits its application.

## Deployment guidance

- Use the default in-process regime when its accepted interpreter limitations fit the application.
  If external isolation is required, configure confinement only where its claim is true for the whole run and confirm it from the authority that established it.
- Back the audit store with storage that survives the process (for example an append-only log or database), not process memory; the in-memory store shipped with the library is for tests and local experiments only.
- Configure forced termination only with a mechanism that can establish actual cessation.
  The terminal's independent UI remains usable while native execution is uninterruptible, but cannot claim that its Session completed or reconstruct a lost Session.
- Keep the host mediation executor installed for the run.
  Explicit configured model selection may change its delegate while retaining the same host, authority ceiling, allocation, and per-request observation.
  No selection resets accounting or introduces fallback.
