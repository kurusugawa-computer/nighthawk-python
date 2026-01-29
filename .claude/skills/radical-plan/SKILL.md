---
name: radical-plan
description: Guides the creation or update of one RadicalPlan to realize an agreed Ideal State document (invariants).
disable-model-invocation: true
argument-hint: "@<ideal-path> <focus>"
---

# Radical plan

This Skill converts an agreed Ideal State document (invariants) into exactly one concrete RadicalPlan.

## Definitions

- Ideal State document: A document whose normative content is invariants only.
  - It may include Open questions and a Decision log for traceability.
  - It must not include implementation steps, gap analysis, roadmaps, migration plans, or task lists.
- Invariant: A normative constraint that should ideally always hold. It is not required to match the current repository state.
- Confirmed invariant: An invariant that is both (a) explicitly confirmed in the session and (b) justified from repository materials.
- Candidate invariant: A proposed invariant that is not yet a confirmed invariant. It must be recorded as a proposal or open question, not as a confirmed invariant.
- Repository materials: Code, tests, and documentation in this repository.
- Plan focus (stage): A label that scopes a single RadicalPlan.
- RadicalPlan: A plan document produced by this Skill. It must meet the RadicalPlan requirements in `PLANS.md`.

### Plan status model (for 2nd+ runs)

These terms exist so `radical-plan` can cheaply narrow scope on later runs without re-reading full plan narratives.

- IdealIds: The set of invariant Ids (`I-...`) found in the Ideal State document.
- PlannedIds: The set of invariant Ids (`I-...`) extracted from checkbox items in the `## Progress` section of existing RadicalPlan files.
  - Existing RadicalPlan files are those in the same directory as the Ideal State document that match:
    - `<ideal-basename>-plan-*.md`
  - Only parse the `## Progress` section and only parse checkbox list items.
  - Do not read other sections to determine status.
- Progress checkbox item: A markdown list item of the form:
  - `- [ ] ... (I-FOO-001)` or `- [x] ... (I-FOO-001, I-BAR-003)`
  - Each Progress checkbox item MUST end with a parenthesized, comma-separated list of invariant Ids.
- Status categories for each invariant Id:
  - Unplanned: The invariant Id is in IdealIds and not in PlannedIds.
  - In progress: The invariant Id appears in one or more Progress checkbox items, and at least one such item is unchecked (`[ ]`).
  - Done: The invariant Id appears in one or more Progress checkbox items, and all such items are checked (`[x]`).

## Core constraints

- Do not create or update a RadicalPlan unless the user explicitly requests it.
- Do not edit repository files unless the user explicitly asks you to edit and there are no unresolved questions or confirmations.
- `radical-plan` may surface candidate invariants as proposals or open questions, but must not record them as confirmed invariants.
- Do not introduce "plan stage invariants". Stages only scope which existing invariants are operationalized in the current RadicalPlan.
- Use stable Ids so the user can respond inline:
  - Proposals: `P-SLUG-001`
  - Questions: `Q-SLUG-001`
  - Confirmations: `C-SLUG-001`
  - Decisions: `D-SLUG-001`
  - Follow-ups: append suffixes (for example, `Q-SLUG-001A`).
- Use ASCII punctuation only.

## Inputs

Treat `$ARGUMENTS` as a topic statement.

Preferred convention:

- `@<ideal-path>`: path to the Ideal State document.
- `<focus>`: what this RadicalPlan will focus on (a stage). Examples: `terminology alignment`, `layout migration stage 1`.

If `@<ideal-path>` is missing, ask for it.

## Workflow

1. Read the Ideal State document and extract invariants.
   - Restate the relevant invariants by invariant Id (`I-...`).
   - If invariants do not have invariant Ids, stop and ask the user to run `radical-chat` to add invariant Ids.

2. If any existing RadicalPlan files exist for this Ideal State document, compute an instant status summary.
   - Locate plan files in the same directory matching `<ideal-basename>-plan-*.md`.
   - Parse only `## Progress` and only checkbox items.
   - Extract `(I-...)` references and compute Unplanned / In progress / Done per invariant Id.
   - Present the summary in the chat (do not write a tracker file).

3. Propose 2-3 possible plan focuses (P-RPLAN-...).
   - Each focus must clearly state what is in scope and what is out of scope.
   - For 2nd+ runs, derive focus options from the status categories:
     - One focus that plans a slice of Unplanned invariants.
     - One focus that finishes In progress invariants.
     - If relevant, one focus that addresses drift between IdealIds and PlannedIds (for example, orphaned Ids in plans).

4. Ask only the questions needed to choose one focus (Q-RPLAN-...).
   - Also list required confirmations (C-RPLAN-...) needed to finalize the plan focus.

5. Do not create or update a RadicalPlan until all of these are settled:
   - plan focus is selected and confirmed
   - all open questions are resolved, deferred explicitly, or recorded as open questions
   - all required confirmations are confirmed

6. Once settled, and only if the user explicitly requests it, create or update exactly one RadicalPlan file in the same directory as the Ideal State document:
   - File name format: `<ideal-basename>-plan-YYYYMMDD-<slug>.md`

## Output contract

1. Restate the input: Ideal State path and proposed plan focus candidates.

2. Restate the relevant invariants by invariant Id (`I-...`).

3. If plan files exist, show an instant status summary derived from `## Progress` checkbox items only:
   - Unplanned: Ids in IdealIds not present in any Progress checkbox item
   - In progress: Ids with at least one unchecked Progress checkbox item
   - Done: Ids with all referenced Progress checkbox items checked

4. Propose plan focus options (P-RPLAN-...), then ask the minimal questions (Q-RPLAN-...) and required confirmations (C-RPLAN-...).

5. Once settled, and only if the user explicitly asks, write or update exactly one RadicalPlan file.

## Traceability overlay (invariant Id references)

When producing a RadicalPlan from an Ideal State document:

- Every Progress checkbox item MUST reference at least one invariant Id (`I-...`) at the end of the line in parentheses.
- If you use milestones, every milestone MUST include a line `Anchored invariants: I-...` referencing at least one invariant Id.

## RadicalPlan requirements

Read and follow `PLANS.md` in this directory. It is the source of truth for RadicalPlan requirements, formatting, and the required skeleton.

## Examples

### Example input

    /radical-plan @docs/ideal/execution.md terminology alignment

### Example expected output shape

- P-RPLAN-01: Focus this RadicalPlan on terminology alignment and defer code movement.
- P-RPLAN-02: Focus this RadicalPlan on code movement stage 1 and defer terminology.

- Q-RPLAN-01: Which focus should we do first?

- C-RPLAN-01: Confirm the selected focus and list explicit out-of-scope items.

(After selection and explicit request, create or update exactly one RadicalPlan file following `PLANS.md`.)
