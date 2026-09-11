# Inspection surface assessment

Date: 2026-09-11.

Retain the production `nh_eval(expression)` and `nh_assign(target_path, expression)` tools, with value inspection and environment discovery through the collision-free runtime helper.
Keep known collisions and exact source-qualified references in the initial context.
The controlled comparison supports this choice for the tested tasks, without establishing universal superiority or equivalent reliability across providers.

## Evidence and controls

The [protocol](20260911-inspection-study-protocol.md) specifies shared production operations, descriptions, continuations, fixtures, budgets, approval rules, and semantic oracles.
Dedicated control and treatment differ only in the initial collision section; dedicated and helper treatments share that section and keep assignment unchanged.
Both surfaces permit ordinary Python helper use.
The dedicated entry point adds its own schema and necessary guidance, so the comparison is not a pure tool-count intervention.
The isolated collision comparison holds the current production-backed tool surface fixed; it does not replay the historical prototype's exact inspection signature.

The primary study has 18 tasks, two models, three variants, two formulations, and two independent executions per formulation: 432 trials.
Jobs were shuffled with seed 20260911.
The 12-trial smoke passed 11 trials; the failure was a duplicated verification call, and the fixtures and scoring were kept unchanged for the primary study.
Guidance variation, another backend, and coding-agent runs are separate studies rather than additional samples pooled into the primary estimate.

Tracked artifacts contain every trial's metrics and every failed model trial's operation trace:

- [828 attempt rows](20260911-inspection-trial-metrics.csv): 804 model trials and 24 explicitly identified preflight settings failures.
- [66 failed model traces](20260911-inspection-failure-traces.jsonl): expressions, operation phases, model calls when available, effects, approvals, and exact score failures.
- [Artifact manifest](20260911-inspection-artifact-manifest.json): configuration and raw-trial hashes, source-manifest hashes, counts, and settings.

The ignored `outputs/20260911-inspection-*` directories retain complete prompts, schemas, observations, append-only trial records, configuration, and copied source files.
The manifest identifies those directories and the line numbers in the metrics identify exact raw records.
The smoke, primary, guidance, and Chat Completions source manifests are identical.
The coding runs add the settings override and include the later variadic-contract fix; these synthetic fixtures do not use variadic outputs or source instrumentation.
Subsequent Python 3.14 annotation, interruption-cleanup, and native-batch validation-order fixes have separate regression and provider checks; the saved model trials are not represented as reruns on those later source revisions.
No credentials are recorded.

## Primary Responses comparison

Intervals are descriptive Wilson 95% intervals for trial outcomes.
Repeated formulations share tasks; the intervals do not establish independent population performance or statistical equivalence.
Input tokens and elapsed seconds include failures.

| Model | Surface | Upfront collisions | Success | Interval | Trials with duplicate domain calls | Mean input tokens | Mean seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Luna | Dedicated | No | 61/72 | 74.7%-91.2% | 7 | 9,788 | 8.6 |
| Luna | Dedicated | Yes | 57/72 | 68.4%-86.9% | 12 | 9,277 | 8.2 |
| Luna | Helper | Yes | 67/72 | 84.8%-97.0% | 3 | 9,442 | 9.3 |
| Sol | Dedicated | No | 72/72 | 94.9%-100.0% | 0 | 8,731 | 7.7 |
| Sol | Dedicated | Yes | 72/72 | 94.9%-100.0% | 0 | 8,731 | 8.1 |
| Sol | Helper | Yes | 72/72 | 94.9%-100.0% | 0 | 8,541 | 9.4 |

For Luna with collisions shown, 56 matched task/formulation/repetition pairs passed both surfaces, 11 passed only the helper surface, one passed only the dedicated surface, and four failed both.
All 72 Sol pairs passed both.
The helper's stronger observed Luna success and fewer duplicate calls matter more than its roughly one-second higher mean latency.
The observed input-token difference is small and inconsistent between models; it is not the selection basis.

On the two explicit source-collision tasks alone, Luna passed 3/8 dedicated-control trials, 4/8 dedicated-treatment trials, and 6/8 helper-treatment trials.
Three control trials selected the wrong Python search function; none of the collision-present treatments did so.
Those control trials made six wrong-source calls in total.
Dedicated-treatment failures instead duplicated the correct task call.
Sol passed all eight trials in each variant.
The full-suite Luna total decreased from 61 to 57 with upfront collisions, so this experiment does not establish a general success-rate improvement from that presentation change.
It does support the intended source-selection mechanism on this small subset, while showing that correct provenance does not prevent duplicate execution.

## Guidance and backend checks

The separate guidance study used `collision`, `helper_collision`, `unbound_helper`, `discovery`, `members`, and `partial_effect`.
Each cell contains 24 trials: six tasks, two formulations, and two repetitions.

| Model | Surface | Short guidance | Example guidance |
| --- | --- | --- | --- |
| Luna | Dedicated | 18/24 | 20/24 |
| Luna | Helper | 21/24 | 23/24 |
| Sol | Dedicated | 24/24 | 24/24 |
| Sol | Helper | 24/24 | 24/24 |

Examples improved the observed Luna totals by two trials on both surfaces.
They did not eliminate duplicated calls or establish a uniquely sufficient prompt.
The helper's ordering relative to the dedicated surface persisted in this separate run.

Chat Completions used all 18 tasks, two formulations, and one repetition per surface/model.

| Model | Dedicated | Helper | Duplicate-call trials, dedicated/helper |
| --- | --- | --- | --- |
| Luna | 30/36 | 33/36 | 5/2 |
| Sol | 36/36 | 36/36 | 0/0 |

The direction agrees with the Responses comparison, with a smaller sample.
This checks a second API transport, not a second model family.

Coding-agent transport checks used six tasks: `collision`, `helper_collision`, `discovery`, `opaque`, `partial_effect`, and `approval_partial`.
There was only one formulation and one execution per surface, so these are diagnostic checks.

| Backend | Dedicated | Helper |
| --- | --- | --- |
| Codex / Luna | 4/6 | 5/6 |
| Claude CLI / Haiku | 2/6 | 2/6 |

Codex failed one dedicated trial by awaiting a synchronous result and another by exceeding the outer usage limit after doing discovery and assigning the answer.
Its helper failure repeated the correct task search call.
Claude selected wrong sources, repeated calls, or omitted the deliberately required failed assignment.
All four coding-agent `approval_partial` trials preserved the approved effect and stopped on the subsequent rejection.
These failures prevent a claim that either surface is reliably followed across all backends.

The initial 24 coding attempts failed before model dispatch because API-only settings were rejected by the coding backends' schemas.
They remain recorded as preflight settings failures and contribute no semantic success estimate.
Corrected runs supply complete backend-specific settings and use separate output directories.
The coding backends expose internal interactions through MCP; the outer Pydantic AI messages do not contain their individual tool-call parts.
Operation records remain available, but empty `model_tool_calls` fields do not mean no tools ran.
Claude's reported input-token field does not represent the same aggregate as Codex's, so cross-backend token comparisons would be misleading.

## Failure interpretation

Success requires the permitted control result and correct values, identities, calls, and effects.
Returning the expected literal does not excuse omitting a required read, validation attempt, approval, or domain operation.

Repeated domain calls are the dominant observed Luna failure pattern.
A common trace evaluates a function to see its answer and then calls the same function again inside assignment.
The runtime executes those two requested operations once each; it cannot silently deduplicate them without changing Python semantics.
The partial-effect task also reveals retries after an already completed effect or omission of the required failed assignment.
Invalid `await` on a synchronous helper result produces a terminal `TypeError` under the ordinary Python exception contract.

The unbound task requires an actual failed read before explicitly selecting an inherited source.
Some Luna trials select the correct inherited value immediately but omit that requested read; they correctly fail the oracle.
Raw lookup totals include these intentional failures and are not a measure of accidental lookup mistakes.
Similarly, `required_read_omissions` in the raw schema counts all `missing_required_*` failures, including omitted validation attempts; use the exact failure labels to distinguish them.
Admission failures include schema-rejected requests and are not synonymous with application exceptions.

The traces show successful progressing discovery on both surfaces and no observed attempt to overwrite the protected helper root.
Some tasks can validly inspect a value through ordinary Python, so helper-name preservation is supported by deterministic tests in addition to model task outcomes.
Optional inspection calls are not automatically unnecessary; wrong-source calls and repeated domain calls have separate metrics.
The `rejected_body_dispatched` label in preflight records also covers absence of the required rejection request; it does not assert that the rejected body ran in those zero-operation attempts.

## Interface selection and limits

Keep `nh_eval` and `nh_assign` as the built-in model tools.
Expose `runtime_reference.inspect(value)` and `runtime_reference.environment` under the environment's allocated collision-free root, and present the exact root and qualified expressions upfront.
Retain cursor-based `discover` and supported-member continuations as complete Python expressions, including when discovery pages are nested in ordinary tool-return containers.
Do not reserve or overwrite an application `inspect` binding, including an unbound one.

The shared runtime already needs environment discovery, static descriptions, rendering, and the operation engine for either surface.
The helper surface additionally uses ordinary Python composition already supported by evaluation.
A dedicated tool would add another public schema, dispatch contract, operation kind, adapter exposure, and parallel set of guidance/continuation examples without removing those shared components.
It remains a useful experiment adapter, but the observed results provide no reliability benefit that justifies that additional supported interface.
This is an assessment of combined implementation and explanation burden, not tool count alone.

The development-visible task set is small, repetitions are correlated, and provider execution is stochastic.
The primary study uses the production executor boundary directly, with synthetic inspection policies and a deterministic child; it does not establish host-policy security, full source-adapter correctness, or actual child-LLM reliability.
Those contracts have separate unit and integration coverage, including the [Natural composition evidence](20260910-natural-composition.md).
Statement/iteration ergonomics and completion redesign remain outside this selection.
No historical merged-tool result is used as evidence for removing dedicated inspection.
