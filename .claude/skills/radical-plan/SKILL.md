---
name: radical-plan
description: Guides the creation or update of one RadicalPlan to realize an agreed Ideal State document (invariants).
argument-hint: "@<ideal-path> <focus>"
---

# Radical plan

This Skill converts an agreed Ideal State document (invariants) into exactly one concrete RadicalPlan.

This Skill is intentionally designed to reduce LLM confusion and avoid infinite or circular planning behaviors:

- The RadicalPlan file MUST NOT contain any Ideal State invariant Ids (no `I-...` anywhere).
- The RadicalPlan file MUST NOT contain the ideal-path.
- The RadicalPlan file MUST be fully self-contained.
- Invariant status (Unplanned/In progress/Done) MUST be tracked outside the plan in a sibling status file.

## Definitions

- Ideal State document: A document whose normative content is invariants only.
  - It may include Open questions and a Decision log for traceability.
  - It must not include implementation steps, gap analysis, roadmaps, migration plans, or task lists.
- Invariant: A normative constraint that should ideally always hold. It is not required to match the current repository state.
- Invariant Id: A stable identifier for an invariant inside a single Ideal State document.
  - Format: `I-<NNN>` or `I-<NNN><SUFFIX>`
  - Uniqueness scope: within the current Ideal State document.
- Status file: A Markdown file that tracks invariant status for one Ideal State document.
  - File name: `<ideal-basename>-status.md` (same directory as the Ideal State document).
  - Required sections: `## In progress` and `## Done`.
  - Done entries MUST include evidence.
- Confirmed invariant: An invariant that is both (a) explicitly confirmed in the session and (b) justified from repository materials.
- Candidate invariant: A proposed invariant that is not yet a confirmed invariant. It must be recorded as a proposal or open question, not as a confirmed invariant.
- Repository materials: Code, tests, and documentation in this repository.
- Plan focus (stage): A label that scopes a single RadicalPlan.
- RadicalPlan: A plan document produced by this Skill. It must meet the RadicalPlan requirements in `PLANS.md` in this directory.

## Plan status model (for 2nd+ runs)

To keep planning cheap and avoid reading large plan narratives, invariant status is derived only from the status file.

- IdealIds: The set of invariant Ids (`I-...`) found in the Ideal State document.
- InProgressIds: The set of invariant Ids (`I-...`) listed under `## In progress` in the status file.
- DoneIds: The set of invariant Ids (`I-...`) listed under `## Done` in the status file.
- Status categories:
  - Unplanned: Id in IdealIds but not in InProgressIds and not in DoneIds.
  - In progress: Id in InProgressIds.
  - Done: Id in DoneIds.

## Core constraints

- Do not create or update a RadicalPlan unless the user explicitly requests it.
- Do not edit repository files unless the user explicitly asks you to edit and there are no unresolved questions or confirmations.
- The RadicalPlan file MUST NOT contain any invariant Ids (`I-...`) anywhere.
- The RadicalPlan file MUST NOT contain the ideal-path.
- The RadicalPlan file MUST be fully self-contained. It must not require reading the Ideal State document to understand what to do.
- Use stable Ids so the user can respond inline:
  - Proposals: `P-KEBAB-001`
  - Questions: `Q-KEBAB-001`
  - Confirmations: `C-KEBAB-001`
  - Decisions: `D-KEBAB-001`
  - Follow-ups: append suffixes (for example, `Q-KEBAB-001A`).
- Use ASCII punctuation only.

## Inputs

Treat `$ARGUMENTS` as a topic statement.

Preferred convention:

- `@<ideal-path>`: path to the Ideal State document.
- `<focus>`: what this RadicalPlan will focus on (a stage). Examples: `terminology alignment`, `layout migration stage 1`.

If `@<ideal-path>` is missing, ask for it.

## Workflow

1. Read the Ideal State document and extract invariants.
   - If invariants do not have invariant Ids, stop and ask the user to run `radical-chat` to add invariant Ids.
   - If invariant Ids do not match the required format (`I-<NNN>` with optional insertion suffix), stop and ask the user to rename them (use `radical-chat`).

2. Read the status file and compute an instant status summary.
   - Locate the status file in the same directory as the Ideal State document:
     - `<ideal-basename>-status.md`
   - If it does not exist, treat all invariants as Unplanned and propose creating the status file.
   - Parse only:
     - `## In progress` section list items
     - `## Done` section list items
   - For `## Done`, verify each listed invariant has evidence recorded (per the status file rules).

3. Propose 2-3 possible plan focuses (P-RPLAN-...).
   - Each focus must clearly state what is in scope and what is out of scope.
   - For 2nd+ runs, derive focus options from the status categories:
     - One focus that addresses a slice of Unplanned invariants.
     - One focus that finishes In progress invariants.

4. Ask only the questions needed to choose one focus (Q-RPLAN-...).
   - Also list required confirmations (C-RPLAN-...) needed to finalize the plan focus.

5. Do not create or update a RadicalPlan until all of these are settled:
   - Plan focus is selected and confirmed.
   - All open questions are resolved, deferred explicitly, or recorded as open questions.
   - All required confirmations are confirmed.

6. Once settled, and only if the user explicitly requests it, create or update exactly one RadicalPlan file in the same directory as the Ideal State document:
   - File name format: `<ideal-basename>-plan-YYYYMMDD-<kebab>.md`

## Output contract

1. Restate the input: Ideal State path (chat-only) and proposed plan focus candidates.

2. Restate the relevant invariants by invariant Id (`I-...`) in chat only.
   - Do NOT copy invariant Ids into the plan file.
   - Prefer grouping and short paraphrases over copying large invariant lists.

3. Show an instant status summary derived only from the status file:
   - Unplanned: Ids in IdealIds not present under `## In progress` or `## Done`.
   - In progress: Ids listed under `## In progress`.
   - Done: Ids listed under `## Done`.

4. Propose plan focus options (P-RPLAN-...), then ask the minimal questions (Q-RPLAN-...) and required confirmations (C-RPLAN-...).

5. Once settled, and only if the user explicitly asks, write or update exactly one RadicalPlan file.

## RadicalPlan requirements

Read and follow `PLANS.md` in this directory. It is the source of truth for RadicalPlan requirements, formatting, and the required skeleton.

## Examples

### Example input

    /radical-plan @docs/ideal/execution-context.md terminology alignment

### Example expected output shape

- P-RPLAN-001: Focus this RadicalPlan on terminology alignment and defer code movement.
- P-RPLAN-002: Focus this RadicalPlan on code movement stage 1 and defer terminology.

- Q-RPLAN-001: Which focus should we do first?

- C-RPLAN-001: Confirm the selected focus and list explicit out-of-scope items.

(After selection and explicit request, create or update exactly one RadicalPlan file following `PLANS.md`.)
