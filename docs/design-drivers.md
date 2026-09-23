# Design drivers

This page records application needs that help explain and evaluate Nighthawk Core design.
The [philosophy](philosophy.md) states the principles, the [specification](specification.md) defines supported behavior, and the [roadmap](roadmap.md) tracks exploratory work.
Application scenarios on this page describe intended compositions; they are not additional runtime guarantees or complete application implementations.

## Workspace information policy

Keep information in this workspace when it improves a decision about Core design, implementation, or verification.
The library package and its development workspace have different inclusion criteria: the workspace also needs rationale, representative applications, experiments, and development tooling.

| Information | Ownership |
|---|---|
| Purpose, Python ecosystem integration, function composition, and human/agent authorship | Core philosophy. |
| Supported runtime behavior and guarantees | Core specification and API reference. |
| Representative application purpose, scenario, and implications for Core | Summaries on this page, linked to detailed application sources when available. |
| General execution gaps discovered through application work | Core design questions and roadmap, with the originating scenario and evidence. |
| Small examples or fixtures that exercise Core contracts | Core guides, tests, or evaluations, maintained with the contracts they exercise. |
| Application architecture, document formats, operational rules, and product plans | Application-owned sources; summarize only what a Core decision needs. |
| Live knowledge collections, user configuration, and operational history | Application-owned storage. |

When an application exposes a gap, first try to express the work through existing functions and libraries.
Extract the general execution requirement before proposing a Core feature.
A useful design record connects the application need, attempted composition, observed limitation, and relevant verification.

Keep one authoritative source for each subject.
When citing external application evidence, identify the relevant version or revision when the conclusion depends on it.
Label a scenario as conceptual, experimentally evaluated, or supported by a documented contract, and link the evidence supporting that status.
Update the summary when its design implications change; detailed application changes remain with their owners.

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
Evaluation should consider evidence retention, valid changes, and actual effects as well as the readability of the resulting text.
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

- A research workflow composes retrieval, evidence assessment, synthesis, and checks on supported claims.
- A business workflow composes interpretation, domain validation, approval policy, and effectful operations.
- An equipment-control workflow composes a proposed plan, deterministic constraints, simulator trials, and environment-owned execution authority.

Use distinct scenarios to test whether an apparent Core requirement is general.
Each application retains its own evaluators, operational rules, and environment; sharing function composition does not establish equivalent correctness or isolation guarantees.

## Hosts selecting bindings at invocation time

**Status:** Supported by the [explicit embedded-step contract](specification.md#explicit-embedded-steps), with deterministic coverage in `tests/runtime/test_embedded.py` and executable [integration examples](embedded-execution.md).

A host can select different trusted Natural programs, read/write names, and Python output contracts at each invocation.
For example, one call summarizes `observations` into a `summary: str`, while another selects from `candidates` into an application-defined `Selection` value.
Passing a dictionary to a fixed Natural function does not by itself declare each dynamic key as a Core binding.
Saving a returned result after that function completes also places save failures outside the Natural step's terminal boundary.

The general requirement is explicit execution-environment selection and host writeback within the existing step lifecycle.
Core supplies those boundaries and shared inference/validation rules; hosts own trusted-source selection, storage, partial-effect recovery, and domain policy.
An ordinary Natural function remains the preferred composition when its fixed signature and completion boundary are sufficient.

## Using Nighthawk to develop Nighthawk

Treat development tooling in this workspace as another application.
For example, a semantic linter could assess documentation against the philosophy and return findings for a Python review policy or development hook.
This is an exploratory use case, not a declaration that such checks are installed or required today.

Keep development-specific rules and automation with their development workflow.
Evaluate their findings against reviewed examples, including false positives and missed violations.
An implementation's presence in this workspace does not make it part of the public runtime API or an obligation for library users.
Evidence of reuse can justify a separate library or a general Core connection point, evaluated against the [Core inclusion criteria](philosophy.md#a-minimal-balanced-core).
