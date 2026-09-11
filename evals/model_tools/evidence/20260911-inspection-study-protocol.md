# Production inspection surface comparison

Status: Completed with deterministic validation and 804 model trials on 2026-09-11.
The [assessment](20260911-inspection-assessment.md) records the selection, failures, controls, and uncertainty.

The entry point is `python -m evals.model_tools.inspection_study`.
It uses production `StepEnvironment`, descriptions, expression and assignment operations, tool normalization, operation inspection, admission records, and `AgentStepExecutor`.
The historical original/refined/merged study remains unchanged.

## Controlled variants

| Variant | Model tools | Initial collision descriptions |
| --- | --- | --- |
| Dedicated control | `nh_eval`, `nh_assign`, `nh_inspect` | Omitted |
| Dedicated with collisions | `nh_eval`, `nh_assign`, `nh_inspect` | Included |
| Helper with collisions | `nh_eval`, `nh_assign` | Included |

The dedicated control and treatment hold their tool definitions and guidance fixed; only the explicit collision section differs.
This isolates presentation on the current production-backed dedicated surface, not the historical prototype's exact inspection signature.
The helper comparison uses the same collision presentation and leaves assignment unchanged.
The dedicated-tool description is an unavoidable guidance difference retained in request snapshots.
`short` and `examples` guidance can be varied independently.

`nh_inspect(expression=None, source="effective", cursor=None, max_items=8)` is an experiment-only adapter entering one inspection operation with the equivalent production helper expression.
It does not introduce another description implementation or cursor store.
Both surfaces receive the same reference and Python continuation expressions, which can be evaluated through `nh_eval`.
The comparison therefore tests adding a dedicated entry point, not prohibiting ordinary Python inspection on one side.
The production package retains its existing two built-in tools.

Discovery values receive a 512-token response budget with the same tokenizer, renderer, page limits, and normalization.
The initial presentation cap is 4,000 tokens for every variant.
Initial Python descriptions omit shadowed-source details outside the controlled collision section.
Lookup, source membership, fixtures, output contracts, approval rules, and semantic oracles are fixed.

## Tasks and measurements

The 18 tasks cover assignment, opaque identity, large values, mutation, source collisions, bound and unbound helper collisions, unbound Python lookup, deterministic nested counters, omitted task names and members, validation recovery, loop continuation, effectful return, approval, rejection, assignment failure after an effect, and rejection after an approved effect.
Each has two formulations.
Domain effects are synthetic in-memory records; no fixture performs external domain work.

Success checks control results, values, required calls, identity, mutation, ordered child counters, required failed reads or validation, and approval effects.
Equivalent valid expressions or tool sequences may pass.
A guessed answer cannot replace a required call or failed operation; duplicate calls are scored separately.

Records retain prompts, instructions, tool/output schemas, model calls, observations, operation phases and exception origins, effects, approvals, response tokens, and elapsed time.
Diagnostics distinguish lookup failures, failed admission, omitted required reads, unnecessary wrong-source domain calls, duplicate calls, and recovery.
Optional inspection calls are not automatically classified as unnecessary.
Each trial is bounded by 32 model requests, 64 tool calls, 100,000 total tokens, and 180 seconds.
Each response permits 6,144 output tokens with a 90-second model timeout and medium reasoning effort.

Jobs are shuffled reproducibly with independent state.
New output directories receive source snapshots, SHA-256 hashes, configuration, append-only trials, and progressive reports.
API headers, credentials, and uncontrolled external exception messages are not recorded.

## External execution

The first check contains 12 trials: six tasks (`collision`, `discovery`, `opaque`, `validation`, `partial_effect`, `approval_partial`), both surfaces, one formulation, one repetition, and `openai-responses:gpt-5.6-luna`.
Requests go to `https://api.openai.com/v1/responses`, authenticated with the `.env` `OPENAI_API_KEY` without displaying it.
Outgoing content is synthetic task text, runtime instructions, schemas, synthetic names/values/callable metadata, and subsequent generated tool calls and synthetic observations.
The API is paid; the bounds above apply to each trial.

```bash
python -m evals.model_tools.inspection_study \
  --models openai-responses:gpt-5.6-luna \
  --cases collision discovery opaque validation partial_effect approval_partial \
  --repetitions 1 --phrasings 1 --concurrency 4 \
  --output evals/model_tools/outputs/20260911-inspection-smoke
```

Add `--prepare-only` and another output directory to export initial request contents with a local `FunctionModel`, without API requests or a key.
Subsequent model-selected calls cannot be predetermined; the available data and tools are the fixed fixtures and operation paths above.
A repeated primary comparison can use Luna and Sol, all 18 tasks, two formulations, two repetitions, and all three variants (`--collision-control`): 432 trials.
Guidance and other backend runs must be labeled separately rather than pooled across settings.
No model preference is selected before results exist.

Locally prepared request inventories are available in the workspace:

- [Initial 12 requests](../outputs/20260911-inspection-request-review-v2/prepared_requests.jsonl).
- [Primary 432 requests](../outputs/20260911-inspection-main-review/prepared_requests.jsonl).

These prepared-request artifacts contain no API trial results; the executed studies use separate output directories listed in the assessment.
Deterministic validation passed 57 study tests; the complete suite passed 968 tests with 31 integration tests skipped.
The separately authorized 13 OpenAI contract integration tests passed after the runtime fixes, before adding this experiment-only harness.
The user resolved the prior execution-review block with `C-INSPECTION-001`, explicitly authorizing all tests using real providers.

The primary comparison completed 432 Responses trials.
Guidance variation completed another 192 Responses trials: six discovery/collision/partial-effect tasks, two models, two surfaces, two guidance settings, two formulations, and two repetitions.
Chat Completions completed 144 trials: all 18 tasks, two models, two surfaces, two formulations, and one repetition.
These runs and the 12-trial smoke used identical source snapshots.

Codex Luna and Claude CLI Haiku each completed 12 additional trials: six tasks, two surfaces, one formulation, and one repetition.
`--model-settings` supplies a complete settings object for these backends instead of the API defaults.
Codex used an isolated temporary working directory and medium reasoning effort; Claude used that directory and `max_turns=32`.
Their 180-second outer trial timeout remained active.
The wrapper's Pydantic AI usage limits apply at outer requests, not as identical per-turn limits inside a coding agent.
Provider token accounting, internal tool messages, and transport guidance differ; do not pool their efficiency measurements with direct API runs.
The preceding 24 coding-backend attempts failed local settings validation before any model request and are excluded from semantic comparisons.

## Limits

The harness enters `StepRequest` directly and checks the resolved candidate and working output values.
Source capture, generated writeback, commit rewriting, and terminal delivery have separate production tests.
The deterministic child reads a materialized production working snapshot; it does not invoke a child LLM.
Actual Natural child calls have separate evidence in `20260910-natural-composition.md`.

Approval is a synthetic expression policy for named direct or source-qualified calls, not a general authorization mechanism.
The oracle rejects missing approvals, wrong effects, and rejected-body execution even when an alternative expression bypasses that fixture policy.
These cases do not establish host-policy security.

The development-visible task set is small.
Repeated formulations are correlated; Wilson intervals are descriptive, not proof of population performance or equivalence.
Semantic reliability and effects precede tokens or latency.
If results do not distinguish acceptable surfaces, compare total runtime, adapter, and guidance complexity; fewer tools alone do not settle the choice.
