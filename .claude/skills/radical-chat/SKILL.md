---
name: radical-chat
description: Guides a radical, repository-wide design alignment session that produces and continuously refines an Ideal State document (invariants).
metadata:
  argument_hint: "@<ideal-path> <topic>"
---

# Radical chat

This Skill is a thinking and alignment guide. It is intentionally radical: prioritize repository-wide coherence over incremental adaptation and over the current repository state.

This Skill is also intentionally artifact-forward: create the Ideal State document early and keep it updated as the session progresses.

## Definitions

- Radical chat: A design alignment session that prioritizes repository-wide coherence over incremental adaptation.
- Ideal State document: The durable shared artifact produced and edited during radical chat. Its normative content is invariants.
- Invariant: A normative constraint that should ideally always hold. It is not required to match the current repository state.
- Invariant Id: A stable identifier for an invariant inside a single Ideal State document.
  - Format: `I-<NNN>` or `I-<NNN><SUFFIX>`
  - `<NNN>` is a 3-digit number (001-999).
  - `<SUFFIX>` is one or more uppercase ASCII letters used for insertion, like spreadsheet columns: `A`..`Z`, then `AA`, `AB`, etc.
  - Invariant Id uniqueness is scoped to the current Ideal State document. Collisions across different Ideal State documents are allowed.
- Repository materials: Code, tests, and documentation in this repository.
  - Repository materials are NON-NORMATIVE inputs by default.
  - They MUST NOT be treated as authority or grounding for invariants.
  - They MAY be used to (a) draft initial proposals, (b) detect conflicts with the current codebase, and (c) check coherence among invariants and existing conventions.
  - Exception: If the user explicitly authorizes using some repository material as a constraint for this session, record that authorization as a Decision, and express the constraint as invariants (not as "because the repo says so").
- Confirmed invariant: An invariant that is explicitly confirmed in the session by the user.
  - Confirmation is a human decision, not a repository-derived fact.
- Open question: A claim that is not yet resolved by an explicit user decision.
  - Open questions are allowed to remain if the user ends the session, but the assistant MUST keep them visible and actionable.
- Decision log entry: A traceability record that supports invariants. It MUST NOT introduce new norms. Decisions exist to explain and support invariants.

## Session rules (radical and persistent)

1. Coherence is a hard constraint.
   - Prefer repository-wide coherence over local optimization.
   - Prefer symmetry, unification, and regularity.

2. The Ideal State document's normative content is invariants only.
   - It may include Open questions and a Decision log for traceability.
   - It MUST NOT include implementation steps, gap analysis, roadmaps, migration plans, or task lists.

3. Be radical: do not let the current implementation constrain the ideal.

4. Anti-drag discipline (repository is not authority):
   - Propose invariants freely.
   - Confirmed invariants require explicit user confirmation.
   - Repository materials are inputs for drafting and coherence checks only; they MUST NOT be used to "prove" or "justify" invariants.
   - Exception: If the user explicitly authorizes treating specific repository materials as constraints, record that authorization as a Decision and express the constraints as invariants.

5. Open questions are persistent and not blocking:
   - If open questions remain, you MUST surface them regularly and ask for resolution.
   - You MUST NOT block progress by default.
   - The user may end the chat session at any time; if they do, you MUST provide a "handoff" summary of remaining open questions and the next confirmations needed to resolve them.
   - If the user defers a question, record the deferral explicitly in Open questions (with a reason).

6. Decisions support invariants (non-normative):
   - Every Decision log entry MUST explicitly reference which invariant(s) it supports.
   - A Decision log entry MUST NOT create new requirements. Requirements belong only in invariants.
   - If a Decision would introduce a new requirement, write that requirement as an invariant and make the Decision support it.

7. Early artifact creation and active editing (Ideal State document only):
   - Create the Ideal State document as early as possible (after selecting an ideal-path).
   - Prefer frequent small edits while chatting, because the user often reads the file.
   - Ask for one-time "standing permission" to keep editing the Ideal State document during the session. If permission is not granted, show a proposed patch instead of editing.

8. Invariant edits trigger global review:
   - Whenever any invariant is added, updated, or deleted, you MUST re-scan and re-evaluate ALL other invariants in the current Ideal State document for coherence and conflicts.
   - If conflicts appear, either (a) propose synchronized edits, or (b) convert the disputed area into open questions until resolved.

9. Invariant Id rules:
   - Every invariant MUST have an invariant Id using this format: `I-<NNN>: <statement>` (or with an insertion suffix: `I-<NNN><SUFFIX>: <statement>`).
   - Renumbering existing invariants is forbidden. Treat Ids as stable anchors.
   - Default rule for new invariants is append-only (next available `I-<NNN>`).
   - Use insertion suffixes only when you must insert between existing invariants.
     - Example: insert between `I-042` and `I-043` as `I-042A`.
     - Further inserts: `I-042B`, ..., `I-042Z`, then `I-042AA`, `I-042AB`, etc.

10. Stable Ids for inline responses:
   - Proposals: `P-KEBAB-001`
   - Questions: `Q-KEBAB-001`
   - Confirmations: `C-KEBAB-001`
   - Decisions (for decision logs): `D-KEBAB-001`
   - Follow-ups: append suffixes (for example, `Q-KEBAB-001A`).

11. Ideal State document isolation:
   - The assistant MUST only read or operate on the Ideal State document explicitly provided via `@<ideal-path>`.
   - The assistant MUST NOT enumerate, open, compare, or reference any other Ideal State documents.
   - Exception: If the user explicitly instructs referencing another Ideal State document, the assistant may do so only for the explicitly specified path(s).

## Arguments

Treat `$ARGUMENTS` as a topic statement.

Preferred convention:

- `@<ideal-path>`: path to the Ideal State document to write or update.
- `<topic>`: the topic statement.

### First-run assumption: `<topic>` only

This Skill assumes the first invocation may provide only `<topic>`.

If arguments omit `@<ideal-path>`:

1. Propose 1-3 ideal-path candidates.
   - Default recommendation: `.agent/ideal/<topic-kebab>.md`
2. Ask the user to choose one path (or provide another).
3. Create the file early (skeleton), once the path is chosen and editing permission is granted.

## Output contract (every turn)

Each assistant response MUST include:

1. Restate the topic and scope.
2. Show proposed invariants (I-...) and/or proposed invariant edits (add/update/delete).
3. List open questions (Q-...) and confirmations (C-...) needed to confirm the next set of invariants.
   - If questions are deferred, mark them explicitly as deferred and keep them visible without blocking progress.
   - Each open question MUST include at least one concrete decision option, plus the confirmation(s) needed to convert it into invariant edits.
4. Decision support (D-...) only when it helps explain invariants.
   - Each decision MUST reference supported invariants and MUST NOT introduce new norms.
5. If an Ideal State document path is known:
   - If standing permission exists: apply edits immediately and keep the document updated.
   - If not: show a proposed patch/diff for the document.

6. Optional closure pass (only if the user asks to finalize):
   - If the user requests closure/finalization, you MUST attempt to drive Open questions to Decisions and confirmations.
   - You MUST still allow the user to end the session at any time; if they stop before closure is complete, provide a handoff summary.

## Ideal State document structure

When writing or updating the Ideal State document, use these sections:

- Purpose
- Definitions
- Invariants
- Non-goals
- Open questions
- Decision log

## Examples

### Example input (full form)

    /radical-chat @.agent/ideal/execution.md execution context

### Example input (first run, topic only)

    /radical-chat execution context

### Example expected output shape (first run)

- P-EXECUTION-001: Propose ideal-path `.agent/ideal/execution-context.md` unless you choose otherwise.
- Q-EXECUTION-001: Which ideal-path should we use for the Ideal State document?
- C-EXECUTION-001: Confirm standing permission for me to create and continuously edit the Ideal State document during this session.
- I-001: Execution context is a defined term and must not have synonyms in code or docs.
- Q-EXECUTION-002: Which conceptual alternatives for "execution context" should the Ideal State choose between (list options, even if tentative)?
- C-EXECUTION-002: Confirm which alternative to adopt (or confirm a new alternative).
- D-EXECUTION-001: Record the chosen alternative as decision support for I-001 (and any new invariants needed).

(After the user confirms permission and resolves or defers the necessary questions, create/update the Ideal State document and keep it in sync. If the user ends the session with open questions remaining, provide a handoff summary.)
