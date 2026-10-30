# Design drivers

This page connects application needs and evidence to decisions about Nighthawk's value and design.
The scenarios ask whether agents construct, reuse, assess, and improve programs more effectively with Nighthawk.
The [philosophy](philosophy.md) supplies the principles and [Core boundary](philosophy.md#core-responsibility), the [specification](specification.md) defines supported behavior, and the [roadmap](roadmap.md) tracks investigations.
Application scenarios on this page describe intended compositions; they are not additional runtime guarantees or complete application implementations.

Label each claim as conceptual, experimentally evaluated, or supported by a documented contract, and link the evidence supporting that status.
Runtime contract coverage establishes specific behavior; claims about task quality, cumulative reuse, or comparative advantage need their own evidence.

## Evaluating Nighthawk's value

Each scenario states an expected benefit and application success conditions.
Compare a Nighthawk implementation with ordinary Python and model libraries using the same tasks, models, available operations, acceptance criteria, and resource limits.
Allow both implementations to use their normal libraries and idioms, and record relevant configuration and version differences.

| Dimension | Evidence to collect |
|---|---|
| Construction and revision | Whether an agent builds a working composition and makes a requested change correctly; misunderstood contracts, repair attempts, and regressions. |
| Results and effects | Whether accepted outputs and actual effects meet application criteria, and whether operation-selection errors, failures, and partial effects can be diagnosed. |
| Reuse and improvement | Success and adaptation effort on a different task, with checks that previously accepted behavior remains valid. |
| Overall complexity | Work required to understand and maintain contracts, context, configuration, host integration, adapters, and recovery, including burden shifted between authoring and execution. |
| Resources | Model usage, latency, and cost during both program construction and execution. |

Use representative cases, including failures and cases outside those used to develop a change.
Account for evaluator limitations and variation across repeated model runs.
These observations assess usability; they do not directly measure an LLM's internal cognitive load.
Record when Nighthawk offers no benefit or increases burden; shorter source or fewer parameters, tools, or model calls alone do not establish success or lower cognitive load.
Keep detailed experiment procedures and results in linked evaluation records.
The scenarios below identify comparisons to perform; they do not establish a comparative advantage.

## From findings to design decisions

Begin with ordinary functions and existing libraries.
A useful design record connects the application need, attempted composition, observed result or limitation, evaluation evidence, and resulting decision.
Possible outcomes include:

- Keep the existing composition when it adequately meets the need.
- Improve documentation, examples, or discovery when an existing feature is difficult to use correctly.
- Extract a reusable library when applications share behavior that existing execution contracts can support.
- Simplify an existing feature when its total burden exceeds its benefit.
- Propose a Core addition when a general execution requirement remains unmet.

For a Core addition, support the [Core inclusion criteria](philosophy.md#core-inclusion-criteria) with:

1. A concrete execution need, shown by an application composition or a reproducible contract example.
2. Evidence that existing composition and reusable libraries cannot meet that need correctly at acceptable total burden.
3. A generality argument explaining why the required execution semantics remain useful when application-specific operations and policies change.

Evidence from independent applications strengthens the case but is not a prerequisite.
Distinguish demonstrated behavior from predicted reuse, and record uncertainties that further application work should resolve.
For a correction to an existing contract, a reproducer and regression coverage can establish the need without another application.
For a simplification, show how it reduces total burden while preserving required contracts, or identify any intentional contract change and its compatibility consequences.
Reuse can justify a library without a Core addition; including a helper or backend policy in the package does not by itself make it part of Core.

For an API change, make the philosophy's [understandable contracts](philosophy.md#agents-can-understand-and-revise-programs) assessable in review:

- Show the ordinary usage path and explain any additional entry points through differences in caller-visible contracts.
- Identify dependencies, effects, failure boundaries, and the owners of resources and policy from public documentation and signatures.
- Demonstrate any ownership distinction through a small executable composition, including rejection or restoration where relevant.
- Place each behavioral check with the contract it protects, following the [test organization](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/tests/README.md); retain boundary coverage while removing checks that only record implementation history.
- Update the authoritative contract and its linked examples together, with explicit compatibility consequences.

## Agents building and improving reusable functions

**Status:** Conceptual application direction; no evaluation of the complete construction, revision, and reuse cycle is recorded here.

An agent receives a task and existing Python functions, such as operations for retrieving evidence, assessing claims, and producing a research report.
It constructs and maintains the program; humans may supply goals or assess consequences.
The expected benefit is that meaningful function contracts and composable Natural judgments reduce construction and adaptation errors while making results and effects assessable.

### Representative workflow

1. Discover available functions and inspect their intent, inputs, outputs, effects, and failure behavior.
2. Compose existing operations with Natural judgments and application acceptance checks.
3. Run the composition and compare its results and effects with the task's acceptance criteria.
4. Use observed failures to revise functions, judgment criteria, or their composition, and check for regressions.
5. Where evaluation supports explicit rules, replace a Natural judgment with Python and assess the change on additional cases.
6. Retain the functions, criteria, and evaluation evidence, then reuse and adapt them for a different task.

Success requires correct construction and revision, useful results on the later task, and preservation of relevant earlier behavior.
Use the shared comparison conditions to measure adaptation effort and total burden against ordinary Python and model libraries.
Successful execution of an isolated function does not establish cumulative reuse.

The design questions concern discoverability, understandable contracts, composition across task boundaries, and useful feedback about failures and effects.
Findings may call for better descriptions or examples, a reusable library, simpler interfaces, or a general execution connection point.
Applications own function storage, evaluation records, revision selection, and improvement processes; this scenario does not require a Core registry, learning system, or self-modification mechanism.

## Agents revising programs during a task

**Status:** Conceptual application direction; no implementation or evaluation is recorded here.
Apply the philosophy's principles for [program authority](philosophy.md#program-text-is-code) and [acceptance ownership](philosophy.md#acceptance-criteria-belong-to-their-owner) throughout the revision workflow.
The existing execution boundaries below support parts of the scenario; the complete workflow remains conceptual.

An agent performing a task finds that a Natural function, judgment criterion, or Python helper is inadequate, revises it, and continues the task with the revision.
Unlike [revision across tasks](#agents-building-and-improving-reusable-functions), the revision takes effect before the task completes.
The expected benefit is recovery from an inadequate program without abandoning the task, while useful revisions remain candidates for later reuse.
The risk is that material read during the task induces a harmful revision, which then persists and affects later tasks.

### Representative workflow

1. Run a composition and observe a result or effect that fails the task's acceptance criteria.
2. Revise judgment instructions, examples, or helpers within the delegated implementation authority, preserving the task's acceptance criteria and authorization policy.
3. Apply the application's authorization policy to the proposed revision and its intended purpose, permissions, and environment. Structural checks, semantic linting, tests, and human review can supply evidence for that policy; no single check proves correctness.
4. Execute the authorized revision through the selected host entry point.
5. Assess the revised result and its effects against the unchanged acceptance criteria, linking the assessment to the revision and execution through [host records](embedded-execution.md#record-revision-evidence).
6. Retain a revision for later tasks when evaluation supports its intended reuse and preservation of required behavior, and host authorization covers that reuse.

Success requires task completion with acceptable effects, execution within the authorized context, and records that distinguish the original program from its revisions.
Evaluation cases should include material that attempts to induce a harmful revision, unauthorized changes to acceptance criteria, and helper-only revisions that leave Natural text unchanged.
Compare with an ordinary Python implementation that generates, authorizes, and executes revised prompts and code under the same comparison conditions.

### Questions this application asks of Core

| Application need | General design question | Existing connection or boundary |
|---|---|---|
| Execute a revision within the running task | Which execution form can accept the revised program? | Applying `@nh.function` to a generated function requires [available Python source](specification.md#812-supported-python-source-forms); [embedded definitions](specification.md#explicit-embedded-steps) accept host-selected Natural text and declarations without Python function source inspection. |
| Authorize the revision in its intended context | Which entry point applies host policy before effects can occur? | Embedded steps bypass `FunctionCallHandler`, so hosts supply an [authorization entry point](embedded-execution.md#authorize-the-execution) for that path. Isolation remains host and backend owned. |
| Connect evidence to the executed revision | Can records distinguish the original program from its revisions? | [Terminal lifecycle records](specification.md#104-terminal-delivery-and-host-ledgers) supply execution evidence; [host records](embedded-execution.md#record-revision-evidence) supply revision provenance. |

These questions guide small examples and evaluations.
They do not require Core to adopt a revision store, promotion workflow, learning system, or self-modification mechanism.
The application owns authorization policy, program storage and history, evaluation, and the decision to retain a revision.
Reusable helpers or libraries can support recording revisions, applying host authorization, and connecting executions to evaluations.
Begin with small examples using existing boundaries; use their limitations as evidence for any proposed Core addition.

## Markdown knowledge management

**Status:** Conceptual application direction.
Natural functions, scoped hooks, and runtime validation exist; the complete knowledge-management workflow below has not been established as an implemented or evaluated application in this workspace.
No separate application reference is recorded here yet.

### Purpose

Humans and agents maintain a collection of Markdown documents containing knowledge, goals, judgment criteria, procedures, and constraints.
Python functions connect interpretation with retrieval, checks, actions, and storage.
The application distinguishes reference material from trusted executable instructions even when both use Markdown.
Knowledge management is one application of Nighthawk alongside business workflows, research, and equipment control.

### Representative workflow

1. Retrieve notes and source material relevant to a proposed knowledge update.
2. Use Natural functions to identify claims, supporting evidence, uncertainty, and conflicts.
3. Produce a proposed change as Python values.
4. Inspect the proposal with structural checks and a semantic linter expressed as a Natural function.
5. Use a Python policy function to accept the findings, request revision, or seek human judgment.
6. Apply an accepted change through an application-owned operation and preserve its evidence and history.

A hook can invoke the checking composition at the chosen execution boundary.
The semantic linter returns findings; the policy interprets them; the hook determines where that policy runs.
Hook execution can ensure that a covered operation receives a check, but does not prove the accuracy of its semantic verdict.
The application owns acceptance thresholds, incomplete assessments, evaluation quality, and any required protection against alternate execution paths.

A useful outcome may preserve conflicting claims or explicitly record uncertainty.
The expected benefit is that composable semantic and structural checks make knowledge updates easier to assess and revise.
Success includes evidence retention, valid changes, appropriate handling of uncertainty, and agreement between the accepted proposal and the actual update.
Compare construction and revision of the workflow with ordinary Python and model libraries, including how easily its checks can be reused for another collection.
The workflow produces knowledge changes and does not require generated code.

### Questions this application asks of Core

| Application need | General design question | Existing connection or boundary |
|---|---|---|
| Inspect a proposal using its evidence and rules | Can a checker receive the required values without reconstructing them from a transcript? | Python arguments, bindings, and deliberately shared task context. |
| Combine semantic and structural checks | Can ordinary and Natural functions exchange meaningful typed findings? | Natural function composition and runtime value validation. |
| Apply a check before a covered action or result | Is there a suitable point to inspect and accept or reject execution? | [Operation inspection](specification.md#103-operation-inspection) and [commit inspection](specification.md#102-host-commit-boundary); earlier effects are not rolled back. |
| Use a Natural checker from a hook | How are nested execution, inherited policy, and unintended hook recursion handled? | An application composition to investigate; no general recursion-handling policy is promised. |
| Distinguish a finding from a broken checker | Can expected domain results and execution failures reach the caller distinctly? | Typed results and Python exception behavior; the application defines domain outcomes. |
| Explain what happened after rejection or interruption | Are results, partial effects, and completion understandable to the host? | [Lifecycle facts](specification.md#104-terminal-delivery-and-host-ledgers), with host-owned durable records. |

These questions guide small examples and evaluations.
They do not require Core to adopt note schemas, a knowledge store, a semantic-linter framework, or an approval product.
The application owns retrieval choices, document conventions, storage, domain rules, concrete actions, permissions, and recovery.

## Other applications as generality checks

**Status:** Conceptual comparison scenarios.

The hypothesis is that the same composition principles reduce construction and revision burden across distinct domains.
Apply the shared comparison conditions with domain-specific acceptance criteria:

- A research workflow composes retrieval, evidence assessment, synthesis, and checks on supported claims; assess evidence coverage, supported conclusions, and adaptation to a new topic.
- A business workflow composes interpretation, domain validation, approval policy, and effectful operations; assess valid outcomes, policy compliance, duplicate effects, and adaptation to changed rules.
- An equipment-control workflow composes a proposed plan, deterministic constraints, simulator trials, and environment-owned execution authority; assess goal achievement, constraint violations, and adaptation to changed conditions within the simulator's stated limits.

Use these scenarios to test both Nighthawk's value and whether an apparent Core requirement is general.
Each application retains its own evaluators, operational rules, and environment; sharing function composition does not establish equivalent correctness or isolation guarantees.

## Hosts selecting bindings at invocation time

**Status:** Supported by the [explicit embedded-step contract](specification.md#explicit-embedded-steps), with deterministic coverage in `tests/runtime/test_embedded.py` and executable [integration examples](embedded-execution.md).
This status covers runtime behavior; comparative authoring and reuse benefits remain unevaluated here.

A host can select different trusted Natural programs, read/write names, and Python output contracts at each invocation.
For example, one call summarizes `observations` into a `summary: str`, while another selects from `candidates` into an application-defined `Selection` value.
Passing a dictionary to a fixed Natural function does not by itself declare each dynamic key as a Core binding.
Saving a returned result after that function completes also places save failures outside the Natural step's terminal boundary.

The general requirement is explicit execution-environment selection and host writeback within the existing step lifecycle.
Core supplies those boundaries and shared inference/validation rules; hosts own trusted-source selection, storage, partial-effect recovery, and domain policy.
An ordinary Natural function remains the preferred composition when its fixed signature and completion boundary are sufficient.

The expected benefit is less custom host integration for dynamic declarations and writeback.
Success requires correct per-invocation value contracts and completion records that reflect writeback failures.
Compare the effort and complexity of constructing and changing this integration with an ordinary Python implementation providing the same guarantees.

## Explicit sharing across nested Natural calls

**Status:** The name-resolution contract is covered by deterministic tests in `tests/runtime/test_explicit_sharing.py`, with executable synchronous and asynchronous [sharing examples](runtime-configuration.md#selecting-shared-values-with-python).
The originating evidence is one embedded conversation host's v0.15.0 reproduction; independent application evidence and model-quality improvements remain unevaluated.

The host calls reusable Natural functions from a step containing conversation input and temporary working values.
Automatically copying those working names into children creates dependencies beyond the child's arguments, Python lexical environment, and deliberately shared references.
A context builder can omit prompt content but cannot change preparation-time binding resolution or runtime name lookup.
Requiring host executors to reconstruct Core preparation would move execution complexity into applications.

Core therefore constructs each child's own Python environment and supplies only the active scope's explicit task references as additional names.
Applications select values using ordinary Python helpers and pass dictionaries through `scope(task_references=...)`; no inheritance mode or environment-construction callback is required.
Scopes preserve execution parentage and existing management contracts while replacing or clearing shared references.
This simplifies an existing execution semantic; it does not establish a general object-isolation facility.

The deterministic evidence covers absent parent bindings, preparation failures, real context construction, expression and discovery access, object identity, nested execution, and scoped asynchronous behavior.
It does not establish that separating names prevents repeated model calls or improves judgments.
Assess those hypotheses through application evaluations, including independent reuse and the effort needed to declare shared dependencies.

## Preserving Python work after Natural generation

**Status:** Experimentally evaluated on synthetic inputs, with one successful corrected run for each of three scenarios on two models.
The [2026-10-21 composition investigation](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/evals/model_tools/evidence/20261021-python-composition.md) records the initial failure, correction, configuration, results, and limitations.

A reporting function generates summary lines and then uses Python to assess each line.
An execution block that permits `return` can bypass that later work.
In the observed failure, the model returned the intermediate summary string, and Core rejected it against the outer function's list return contract.
The intermediate value's valid type did not establish task completion.

The existing `deny: [return]` contract adequately expresses the required boundary.
The corrected fixture and [composition guide](patterns.md#python-iteration-with-natural-judgment) apply it before the Python loop.
This finding calls for explicit application contracts and verified guidance, without an additional Core primitive.
The small model sample does not establish broad reliability, lower authoring effort, or comparative advantage.

## Correcting refused operations within a Natural step

**Status:** Execution contracts have deterministic coverage in `tests/runtime/test_operation_recovery.py` and `tests/backends/test_rejection_feedback.py`, with executable [model and custom-executor examples](operation-feedback.md).
No independent application case or comparative model-quality evaluation is recorded.

A host may refuse a proposed operation while still permitting a different way to complete the task.
Previously every operation-inspection rejection ended the step, even if an executor caught the exception.
Ordinary helper return values can communicate expected domain findings, but cannot change that Core-owned inspection and finalization contract.
Reimplementing the operation pipeline in application executors would duplicate state, notification, and termination responsibilities.

Core therefore separates `RejectOperation`, which refuses one body and exposes deliberate feedback, from `AbortStep`, which ends the inspected step.
This requirement concerns the execution meaning of refusal, independently of the application policy that causes it.
The same step retains its live environment, prior effects, model history, and request controls while the model or custom executor chooses another operation.
Refusal records survive successful finalization; failed notifications and terminal stops cannot become ordinary feedback.
Visible model batches stop at refusal, while provider-internal batches remain a documented backend boundary.

Applications own the policy, alternative operations, acceptance criteria, and any durable evidence or improvement process.
Core does not infer that a later accepted output means the task was completed, automatically retry an operation, or add a separate repair agent or policy engine.
The deterministic tests establish execution behavior, not improved judgment, cumulative reuse, or comparative advantage.
Further application evidence should compare successful corrections, unacceptable effects, authoring and maintenance burden, and inference cost against ordinary Python/model-library compositions.

## Using Nighthawk to develop Nighthawk

**Status:** Conceptual application direction.

Treat development tooling in this workspace as another application.
For example, a semantic linter could assess documentation against the philosophy and return findings for a Python review policy or development hook.
This is an exploratory use case, not a declaration that such checks are installed or required today.

Keep development-specific rules and automation with their development workflow.
The [development program policy](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/CONTRIBUTING.md#programs-used-in-development) defines authorization for experiments that generate or revise program assets and distinguishes experiment execution from adoption of its outputs.
Evaluate their findings against reviewed examples, including false positives and missed violations.
The expected benefit is reusable checks that agents can improve as documentation and design rules change.
Success includes useful findings, acceptable review burden, and correct reuse on another document set.
Compare construction and maintenance with ordinary Python and model libraries under the same evaluation conditions.
An implementation's presence in this workspace does not make it part of the public runtime API or an obligation for library users.
Use the [design decision process](#from-findings-to-design-decisions) to decide whether the evidence supports keeping the composition, improving guidance, extracting a library, simplifying a feature, or changing Core.

## Workspace information policy

Keep information in this workspace when it improves a decision about Nighthawk's value, usability, design, implementation, or verification.
The library package and its development workspace have different inclusion criteria: the workspace also needs rationale, representative applications, experiments, and development tooling.

| Information | Authoritative source |
|---|---|
| Purpose, foundational design rationale, and principles for evaluating tradeoffs | Philosophy. |
| Guidance on when to use Nighthawk | README and documentation home, with concise summaries of the philosophy. |
| Supported runtime behavior and public interfaces | Specification for behavior; API reference for signatures and symbols. |
| Application needs, value hypotheses, findings, and resulting design decisions | Summaries on this page, linked to application sources and evaluation records with methods, results, and limitations. |
| Open design questions and the evidence to seek next | Roadmap, linked to the originating scenario or finding. |
| Application of supported contracts | Task-oriented guides and their executable examples; tests cover the contracts they exercise. |
| Documentation responsibilities and editorial constraints | [Documentation rules](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/.claude/rules/docs.md). |
| Development setup, change and review procedures, verification, and experiment authorization | [CONTRIBUTING](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/CONTRIBUTING.md). |
| Application architecture, document formats, operational rules, and product plans | Application-owned sources; summarize what a decision about Nighthawk needs. |
| Live knowledge collections, user configuration, and operational history | Application-owned storage. |

Keep one authoritative source for each subject; standalone guides and entry pages may repeat concise summaries with links to it.
When citing external application evidence, identify the relevant version or revision when the conclusion depends on it.
Update a summary when its design implications change; detailed application changes remain with their owners.
