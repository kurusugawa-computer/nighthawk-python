# Tool-interface assessment, 2026-09-09

Historical GPT-4.1 assessment. The later requested
[GPT-5.6 Luna/Sol assessment](20260909-gpt56-assessment.md) revises the failure
diagnosis and follow-up priorities. Preserve these results as historical evidence.

The refined three-tool surface is a provisional design baseline, not an
experimentally established winner. Neither fewer tools nor more examples
consistently improved complete task success. Keep the ratified reference,
inspection, and response contracts as design candidates; resolve the observed
interaction failures before claiming that the interface reduces model burden.

## Completed experiments

The main comparison completed 384 trials: 16 synthetic cases, three surfaces,
two models (`openai:gpt-4.1` and `openai:gpt-4.1-mini`), two formulations, and
two repetitions, with example guidance. Overall success includes correct
control flow, values, object identity, required calls, and effects.

| Surface | Overall success | State/effect success |
| --- | --- | --- |
| Original three tools | 53/128 | 74/128 |
| Refined three tools | 44/128 | 70/128 |
| Merged two tools | 43/128 | 84/128 |

The merged surface more often completed the required state changes, but also
more often chose the wrong completion kind. Removing only wrong-control-kind
failures gives the secondary state/effect score; it is not a relaxed acceptance
criterion. Model-specific results differ substantially, so pooled counts cannot
establish a generally preferred surface. See the [main report](20260909-main.md).

A separate description comparison completed 192 trials: eight cases, three
surfaces, two models, two descriptions, two repetitions, and one formulation.

| Surface | Short overall | Examples overall | Short state/effects | Examples state/effects |
| --- | --- | --- | --- | --- |
| Original | 11/32 | 14/32 | 16/32 | 18/32 |
| Refined | 12/32 | 10/32 | 18/32 | 13/32 |
| Merged | 13/32 | 9/32 | 25/32 | 22/32 |

Across 96 matched description pairs, examples won 8, tied 77, and lost 11 on
overall success. This does not support a general benefit from longer example
guidance. See the [description report](20260909-guidance.md).

These studies use different task sets and prototype versions and must not be
pooled. The main version incorrectly reported `evaluation_started=True` for
syntax errors. Before the description study, compilation was moved before that
flag and syntax feedback was made actionable. Trace serialization was also
hardened against cyclic values. This correction particularly affects refined
and merged feedback; the main study therefore does not establish the comparative
performance of the corrected interface. Description comparisons within the
second study use the same corrected version.

## Diagnosed interaction failures

- Name collisions were usually invisible to the model's chosen strategy. No
  main-study collision trial used inspection (0/16 per surface). A reference
  mechanism cannot help if the model does not recognize that it needs discovery.
- Models repeatedly passed assignment statements to an expression-only tool.
  All surfaces had syntax-error loops. Actionable syntax feedback is now covered
  by a deterministic test; exposing a natural mutation operation still needs a
  design decision.
- A comprehension's `i` was mistaken for the materialized parent-step counter.
  Child calls then observed no counter and produced ten ordinary results. The
  fixture is solvable by assigning the shared `i` before each awaited call, as
  verified by a deterministic test. That route's many interactions remain a
  usability cost, not merely a missing prompt example.
- Correct assignments often ended with `return` instead of `pass`. The same
  structured completion contract was used across surfaces. Tool design and
  completion design therefore need evaluation together.
- Some return tasks invoked `issue_receipt()` and then supplied that expression
  again as `return_expression`, creating a second effect. Retention and finish
  guidance did not reliably prevent this.

## Proposed next design decisions

These are new proposals, not ratified production behavior.

- **P-TOOLS-005:** Present known name collisions and their qualified references
  in the initial context, while keeping inspection for details and omitted names.
  This makes source selection possible without first guessing that a collision
  exists.
- **P-TOOLS-006:** Revisit expression-only execution for mutation and ordered
  loops. Compare explicit mutation and iteration operations against a trusted
  Python statement tool, with materialized parent state specified in either
  design. Executing model-written Python statements would remain distinct from
  compiling Natural prose into Python, which is excluded.
- **P-TOOLS-007:** Evaluate completion jointly with retained values: clarify
  ordinary block completion and provide a way to return an already retained
  result without reevaluating the expression that produced it.

## Evidence and limits

All 576 completed trials use the OpenAI transport through Pydantic AI 2.37.0.
No coding-agent or Anthropic transport was evaluated. The tasks are synthetic,
development-visible, and correlated across formulations. The child block is a
deterministic async fixture, approvals are synthetic, and production frame
capture, initial-value type inference, and lifecycle/ledger behavior are outside
this prototype. These observations measure behavior and interaction costs,
not cognition directly or production-runtime readiness.

The smoke study and the interrupted first main attempt are excluded. That
attempt stopped after 32 persisted trials because an unexpected coroutine value
could not be serialized; the complete main study started with fresh workspaces.
No failed or incomplete trial from either completed study was removed.

Generated reports include per-model counts, descriptive Wilson intervals,
usage, latency, task breakdowns, matched surface comparisons, and failure
diagnostics. Operation counts exclude structured completion and schema-rejected
calls before workspace entry; observed errors include intentional failures.
Raw `unexpected_tool_errors` is an allowance-adjusted diagnostic, not a full
classification. Small correlated samples do not justify population-level
significance claims.

The tracked [trial metrics](20260909-trials.csv),
[configurations](20260909-configurations.json), and
[selected traces](20260909-selected-traces.json) preserve reviewable evidence.
Full synthetic prompts, schemas, traces, and matching source snapshots remain
locally under the ignored `outputs/main-v2-20260909/` and
`outputs/guidance-20260909/` directories. Re-running today's source is a new
experiment, not a reproduction of the historical main prototype.
