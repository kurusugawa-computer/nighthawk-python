---
name: radical-chat
description: Guides a radical, workspace-wide design alignment session that produces or updates an Ideal State document (invariants).
disable-model-invocation: true
argument-hint: "@<ideal-path> concept:|naming:|procedure:|layout: <topic>"
---

# Radical chat

This Skill is a thinking and alignment guide. It is intentionally radical: once invoked, prioritize global coherence across the workspace over incremental adaptation.

## Definitions

- Radical chat: A design alignment session that prioritizes workspace-wide coherence over incremental adaptation.
- Ideal State document: The durable shared artifact produced or updated by radical chat. Its normative content is invariants.
- Invariant: A normative constraint that should ideally always hold. It is not required to match the current repository state.
- Confirmed invariant: An invariant that is both (a) explicitly confirmed in the session and (b) justified from repository materials.
- Repository materials: Code, tests, and documentation in this repository.
- Open question: A claim that cannot be justified from repository materials and therefore must not be recorded as a confirmed invariant.
- Ideal Slug: The string used inside invariant Ids. It is either explicitly recorded in the Ideal State document as `Ideal Slug: <IDEAL-SLUG>`, or derived from the file basename rule below.

## Session rules

1. Coherence is a hard constraint. Prefer workspace-wide coherence over local optimization.
2. The Ideal State document's normative content is invariants only.
   - It may include Open questions and a Decision log for traceability.
   - It must not include implementation steps, gap analysis, roadmaps, migration plans, or task lists.
3. Be radical: maximize regularity, symmetry, and unification. Do not let the current implementation constrain the ideal.
4. Propose invariants freely, but only record confirmed invariants when they are justified from repository materials and explicitly confirmed.
5. If grounding is not available, keep the claim as an open question (even if it seems like a good idea).
6. Every invariant MUST have an invariant Id using this format:
   - `I-<IDEAL-SLUG>-###: <statement>`
   - `<IDEAL-SLUG>` defaults to the Ideal State document basename (without extension) uppercased.
   - If the basename is generic or ambiguous (for example `ideal.md`, `index.md`, `readme.md`), ask the user for the Ideal Slug to use and record it in the document as `Ideal Slug: <IDEAL-SLUG>`.
   - `radical-chat` MUST NOT derive the slug from directory names, prefixes like `ideal-`, or any other metadata. Use only the explicit `Ideal Slug` line or the basename rule above.
7. Use stable Ids so the user can respond inline:
   - Proposals: P-SLUG-001
   - Questions: Q-SLUG-001
   - Confirmations: C-SLUG-001
   - Decisions (for decision logs): D-SLUG-001
   - Follow-ups: append suffixes (for example, Q-SLUG-001A).

## Arguments

Treat `$ARGUMENTS` as a topic statement.

Preferred convention:

- `@<ideal-path>`: path to the Ideal State document to write or update.
- Exactly one primary dimension tag:
  - `concept:` conceptual structure and definitions
  - `naming:` naming rules and vocabulary
  - `procedure:` process and decision structure
  - `layout:` directory or module layout structure

If arguments omit `@<ideal-path>`, ask the user for a target file path before proposing edits.
If the dimension tag is missing or multiple dimension tags are provided, ask the user which one to use.

## Output contract

1. Restate the topic and scope.
2. Propose the Ideal State as invariants (not steps). Every invariant uses an invariant Id (`I-...`).
3. List open questions (Q-...) and confirmations (C-...).
4. Once resolved, and only if the user explicitly asks, apply edits to the Ideal State document.

## Ideal State document structure

When writing or updating the Ideal State document, use these sections:

- Purpose
- Ideal Slug (optional; only when basename is generic or ambiguous)
- Definitions
- Invariants
  - Concept invariants
  - Naming invariants
  - Procedure invariants
  - Layout invariants
- Non-goals
- Open questions
- Decision log

## Examples

### Example input

    /radical-chat @docs/ideal/execution.md concept: execution context

### Example expected output shape

- P-TASK-001: Draft Ideal State invariants for `docs/ideal/execution.md` under the `concept` dimension.
- I-EXECUTION-0001: Execution Context is a defined term and must not have synonyms in code or docs.
- Q-TERM-001: Which files currently define execution terminology that the Ideal State must reconcile?
- C-TERM-001: Confirm we will treat "Execution Context" as a single defined term and forbid synonyms in code and docs.

(After Q/C are resolved and the user explicitly asks to edit, update the file.)
