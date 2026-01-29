# RadicalPlans: requirements

## Contents

- Purpose and role
- Non-negotiable requirements
- Formatting rules
- Observability and auditability
- Guidelines
- Milestones
- Traceability overlay (invariant Id references)
- Living plans and design decisions
- Prototyping milestones and parallel implementations
- Skeleton of a good RadicalPlan

## Purpose and role

A RadicalPlan is a plan document that a coding agent can follow to deliver a working feature or system change.

Treat the reader as a complete beginner to this repository: they have only the current working tree and the single RadicalPlan file you provide. There is no memory of prior plans and no external context.

RadicalPlans are also a transparency artifact. Assume a human will read the RadicalPlan to observe what was decided, what was done (or planned next), and why. The `Progress` checklist is the task list, and the living sections (`Surprises & Discoveries`, `Decision Log`, `Outcomes & Retrospective`) are the durable record of reasoning and evidence.

## Non-negotiable requirements

- Every RadicalPlan must be fully self-contained. Self-contained means that in its current form it contains all knowledge and instructions needed for a novice to succeed.
- Every RadicalPlan is a living document. Contributors are required to revise it as progress is made, as discoveries occur, and as design decisions are finalized. Each revision must remain fully self-contained.
- Every RadicalPlan must enable a complete novice to implement the feature end-to-end without prior knowledge of this repository.
- Every RadicalPlan must produce a demonstrably working behavior, not merely code changes to "meet a definition".
- Every RadicalPlan must define every term of art in plain language or do not use it.

Purpose and intent come first. Begin by explaining, in a few sentences, why the work matters from a user's perspective: what someone can do after this change that they could not do before, and how to see it working. Then guide the reader through the exact steps to achieve that outcome, including what to edit, what to run, and what they should observe.

The implementer of your plan does not know any prior context and cannot infer what you meant from earlier milestones. Repeat any assumption you rely on. Do not point to external blogs or docs; if knowledge is required, embed it in the plan itself in your own words. If a RadicalPlan builds upon a prior RadicalPlan and that file is checked in, incorporate it by reference. If it is not, you must include all relevant context from that plan.

## Formatting rules

Format and envelope are simple and strict.

- When you present a RadicalPlan inside a chat message, present it as one single fenced code block labeled as `md` that begins and ends with triple backticks.
- Do not nest additional triple-backtick code fences inside. When you need to show commands, transcripts, diffs, or code, present them as indented blocks within that single fence.
- Use indentation for clarity rather than code fences inside a RadicalPlan to avoid prematurely closing the RadicalPlan's code fence.
- Use two newlines after every heading.
- Use `#` and `##` and so on.

When writing a RadicalPlan to a Markdown (.md) file where the content of the file is only the single RadicalPlan, omit the triple backticks.

Write in plain prose. Prefer sentences over lists. Avoid checklists, tables, and long enumerations unless brevity would obscure meaning. Checklists are permitted only in the `Progress` section, where they are mandatory. Narrative sections must remain prose-first.

## Observability and auditability

A RadicalPlan is most valuable when it is easy to audit. Make the plan readable as a running log of decisions and results.

- Use `Progress` as the canonical task list. It should tell a reader, at a glance, what is done, what is next, and what is partially done.
- Use `Decision Log` to record choices as they are made. Include alternatives considered and the concrete reason you picked one.
- Use `Surprises & Discoveries` to capture unexpected behaviors and the evidence that proved them (short command output, error messages, a failing test name, etc.).
- Use `Concrete Steps` and `Validation and Acceptance` to make work reproducible. Prefer commands + expected outputs over vague descriptions.
- Do not rely on ephemeral chat-only reasoning. If it mattered to the approach, it belongs in the RadicalPlan.

## Guidelines

Self-containment and plain language are paramount. If you introduce a phrase that is not ordinary English ("daemon", "middleware", "RPC gateway", "filter graph"), define it immediately and remind the reader how it manifests in this repository (for example, by naming the files or commands where it appears). Do not say "as defined previously" or "according to the architecture doc". Include the needed explanation here, even if you repeat yourself.

Avoid common failure modes. Do not rely on undefined jargon. Do not describe "the letter of a feature" so narrowly that the resulting code compiles but does nothing meaningful. Do not outsource key decisions to the reader. When ambiguity exists, resolve it in the plan itself and explain why you chose that path. Err on the side of over-explaining user-visible effects and under-specifying incidental implementation details.

Anchor the plan with observable outcomes. State what the user can do after implementation, the commands to run, and the outputs they should see. Acceptance should be phrased as behavior a human can verify rather than internal attributes. If a change is internal, explain how its impact can still be demonstrated (for example, by running tests that fail before and pass after, and by showing a scenario that uses the new behavior).

Specify repository context explicitly. Name files with full repository-relative paths, name functions and modules precisely, and describe where new files should be created. If touching multiple areas, include a short orientation paragraph that explains how those parts fit together so a novice can navigate confidently. When running commands, show the working directory and exact command line. When outcomes depend on environment, state the assumptions and provide alternatives when reasonable.

Be idempotent and safe. Write the steps so they can be run multiple times without causing damage or drift. If a step can fail halfway, include how to retry or adapt. If a migration or destructive operation is necessary, spell out backups or safe fallbacks. Prefer additive, testable changes that can be validated as you go.

Validation is not optional. Include instructions to run tests, to start the system if applicable, and to observe it doing something useful. Describe comprehensive testing for any new features or capabilities. Include expected outputs and error messages so a novice can tell success from failure.

Capture evidence. When your steps produce terminal output, short diffs, or logs, include them as indented examples. Keep them concise and focused on what proves success.

## Milestones

Milestones are narrative, not bureaucracy. If you break the work into milestones, introduce each with a brief paragraph that describes the scope, what will exist at the end of the milestone that did not exist before, the commands to run, and the acceptance you expect to observe. Keep it readable as a story: goal, work, result, proof.

Each milestone must be independently verifiable and incrementally implement the overall goal of the plan.

## Traceability overlay (invariant Id references)

If the RadicalPlan exists to realize an Ideal State document:

- Every Progress checkbox item MUST reference at least one invariant Id (I-...).
- If the plan uses milestones, every milestone MUST include a line `Anchored invariants: I-...` referencing at least one invariant Id.

## Living plans and design decisions

- RadicalPlans are living documents. As you make key design decisions, update the plan to record both the decision and the thinking behind it. Record all decisions in the `Decision Log` section.
- RadicalPlans must contain and maintain a `Progress` section, a `Surprises & Discoveries` section, a `Decision Log`, and an `Outcomes & Retrospective` section. These are not optional.
- When you discover optimizer behavior, performance tradeoffs, unexpected bugs, or inverse/unapply semantics that shaped your approach, capture those observations in the `Surprises & Discoveries` section with short evidence snippets.
- If you change course mid-implementation, document why in the `Decision Log` and reflect the implications in `Progress`.

## Prototyping milestones and parallel implementations

It is acceptable and often encouraged to include explicit prototyping milestones when they de-risk a larger change. Examples: adding a low-level operator to a dependency to validate feasibility, or exploring two composition orders while measuring optimizer effects.

Prefer additive code changes followed by subtractions that keep tests passing. Parallel implementations are fine when they reduce risk or enable tests to continue passing during a large migration. Describe how to validate both paths and how to retire one safely with tests.

## Skeleton of a good RadicalPlan

```md
# <Short, action-oriented description>

This RadicalPlan is a living document and a transparency artifact. Assume a human will read it to observe decisions, progress, and evidence. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

## Purpose / Big Picture

Explain in a few sentences what someone gains after this change and how they can see it working. State the user-visible behavior you will enable.

## Progress

Use a list with checkboxes to summarize granular steps. Every stopping point must be documented here, even if it requires splitting a partially completed task into two ("done" vs. "remaining"). This section must always reflect the actual current state of the work.

- [x] (<YYYY-MM-DD HH:MMZ>) Example completed step.
- [ ] Example incomplete step.
- [ ] Example partially completed step (completed: X; remaining: Y).

## Surprises & Discoveries

Document unexpected behaviors, bugs, optimizations, or insights discovered during implementation. Provide concise evidence.

- Observation: ...
  Evidence: ...

## Decision Log

Record every decision made while working on the plan in the format:

- Decision: ...
  Rationale: ...
  Date/Author: ...

## Outcomes & Retrospective

Summarize outcomes, gaps, and lessons learned at major milestones or at completion. Compare the result against the original purpose.

## Context and Orientation

Describe the current state relevant to this task as if the reader knows nothing. Name the key files and modules by full path. Define any non-obvious term you will use.

## Plan of Work

Describe, in prose, the sequence of edits and additions. For each edit, name the file and location (function, module) and what to insert or change. Keep it concrete and minimal.

## Concrete Steps

State the exact commands to run and where to run them (working directory). When a command generates output, show a short expected transcript so the reader can compare. This section must be updated as work proceeds.

## Validation and Acceptance

Describe how to start or exercise the system and what to observe. Phrase acceptance as behavior, with specific inputs and outputs. If tests are involved, say "run <project's test command> and expect <N> passed; the new test <name> fails before the change and passes after".

## Idempotence and Recovery

If steps can be repeated safely, say so. If a step is risky, provide a safe retry or rollback path. Keep the environment clean after completion.

## Artifacts and Notes

Include the most important transcripts, diffs, or snippets as indented examples. Keep them concise and focused on what proves success.

## Interfaces and Dependencies

Be prescriptive. Name the libraries, modules, and services to use and why. Specify the types, traits/interfaces, and function signatures that must exist at the end of the milestone.

## Plan Revision Note

When you revise this plan, append a short note at the bottom describing what changed and why.
```

If you follow the guidance above, a single, stateless agent or a human novice can read your RadicalPlan from top to bottom and produce a working, observable result. That is the bar: self-contained, self-sufficient, novice-guiding, outcome-focused.

When you revise a plan, ensure your changes are comprehensively reflected across all sections, including the living document sections, and append a revision note describing what changed and why.
