# Model-facing tool experiments

This evaluation workspace compares model-facing tool designs through isolated prototypes and production-backed studies.

The original/refined/merged surfaces below retain the 2026-09-09 experiment contracts.
The production-backed dedicated-tool versus helper comparison is implemented separately in `inspection_study.py`.
Its [assessment](evidence/20260911-inspection-assessment.md) records 804 model trials and selects the existing evaluation/assignment surface with Python inspection helpers.
Its [protocol](evidence/20260911-inspection-study-protocol.md) describes controls, limits, source snapshots, and backend differences.
The current production-backed adapter explicitly supplies historical parent fixture values through task references and publishes working snapshots to the Python child fixture's captured workspace.
Production no longer has a parent name source; the recorded 2026-09-11 prompts and results describe the runtime used for those trials, not a rerun of the current adapter.
The [specification](../../docs/specification.md#825-value-inspection-and-name-discovery) owns runtime semantics and the selected inspection/discovery interface.
In particular, the existing `merged` surface merges evaluation and assignment;
it provides no evidence about removing the dedicated inspection tool.

## Approved inspection-helper comparison

This approved plan has deterministic validation and completed model comparisons, reported separately from the historical prototype below.

P-REVIEW-005 approves showing known collisions and qualified references upfront.
Evaluate its isolated effect while holding the existing tool surface fixed.
Then apply the same collision presentation to both sides of the P-INSPECT-002
comparison:

1. Dedicated discovery through `nh_inspect`, alongside evaluation and assignment.
2. Value inspection and environment APIs through `nh_evaluate`, alongside assignment.

Hold discovery behavior, fixtures, lookup, approval rules, budgets, and response
content constant. Keep the assignment interface unchanged; the historical
`merged` variant combined evaluation and assignment and does not test this new
comparison. Record unavoidable differences in usage guidance and report them
rather than attributing every difference to tool count. Vary guidance separately.

Measure semantic success, helper discovery and source-selection failures, invalid requests, unnecessary calls, duplicate effects, recovery, model-visible tokens, and end-to-end latency.
Include helper-name collisions, omitted names/members, large and opaque values, explicitly shared nested working values, validation, completion, and approval with partial effects.
Distinguish runtime lookup failures from omitted explicitly required reads.
Success oracles must allow different valid call sequences while checking required effects and calls.

Use independent repetitions, varied task formulations, and representative models and backends; report sample sizes and uncertainty.
A deterministic child fixture tests shared-state mechanics and model tool use.
Actual Natural parent/child composition requires separate integration coverage.

Apply the ratified
[selection criteria](../../docs/verification.md#interface-evaluation-criteria) when reviewing
the comparison. Prioritize semantic success, permitted control results, and
correct effects over operation count, tokens, or latency. When the evidence does
not distinguish acceptable candidates, prefer lower implementation, explanation,
and maintenance burden across the runtime, adapters, and model guidance. Report
tradeoffs and uncertainty; fewer tools alone do not justify selection.

## Surfaces

| Surface | Tools | Distinguishing behavior |
| --- | --- | --- |
| `original` | `nh_evaluate`, `nh_assign`, `nh_inspect` | Whole-expression environment selection; bounded inspection without continuation. Explicit environment mappings are documented upfront so mixed-environment tasks remain solvable. |
| `refined` | `nh_evaluate`, `nh_assign`, `nh_inspect` | Effective evaluation; inspection returns composable reference expressions and continuation cursors; responses distinguish success, retained destinations, and whether failed evaluation started. |
| `merged` | `nh_evaluate`, `nh_inspect` | Same discovery and response behavior as refined, but `nh_evaluate` takes an optional `target_path` to retain its value. |

The original surface is an executable interpretation of the original ideal
design, not the existing production library. All three share the same lookup,
callables, validation, assignment eligibility, preview length, initial prompt
contents, and sequential tool execution. They differ in the documented tool
surface and its necessary usage guidance. The original/refined comparison is a
bundle of interface changes; it cannot attribute an effect to one changed field.

Run `short` and `examples` guidance separately to compare descriptions while
holding a surface fixed. Examples use the correct destination parameter for
both split and merged tools. Scoring uses final values, identity, actual effects,
required domain calls, and control results, not a prescribed tool-call sequence.

## Concrete interaction contract

The refined signatures are:

```text
nh_evaluate(expression: str)
nh_assign(target_path: str, expression: str)
nh_inspect(name: str | None = None, environment="effective", cursor: str | None = None)
```

Inspection selects `effective`, `python`, `parent`, `task`, or `step`. A returned
reference can be composed with ordinary local names, for example:

```python
nh_assign("result", "nh_environment['task']['search_documents'](question)")
```

`nh_environment` is an expression-only, shallow read-only mapping of environment
views, allocated only when that name is free. If occupied, the prototype chooses
`nh_environment_2`, then the next free suffix. The response supplies the exact
reference; the model does not guess it or import a module. Ordinary lookup keeps
the application's same-name object. Tracked assignment cannot overwrite the
allocated helper root. These guarantees cover the prototype's step-entry
snapshots, not live Python frame mutation.

Inspection returns up to four entries. `next_cursor` continues the same captured
listing using `nh_inspect(cursor=...)` alone. Reusing a cursor returns the same
entries, though its newly emitted continuation token may differ. Name additions
do not alter an existing listing; a new inspection starts a fresh snapshot.
Value previews are bounded observations and can become stale. Reference
expressions resolve the selected name when subsequently evaluated; a preview is
not a deep snapshot or a guarantee that the binding has not changed.

Named inspection includes an object's public instance fields, paginated with
the header. Collection contents use focused Python slices rather than a second
value-paging interface. The prototype deliberately does not implement production
descriptor, slots, multimodal, or arbitrary object-rendering support.

## Running

Use the existing project environment and load the configured API credentials:

```bash
set -a
source .env
set +a
uv run --no-sync python -m evals.model_tools.run \
  --models openai:gpt-5.6-luna openai:gpt-5.6-sol \
  --repetitions 2 --phrasings 2 --concurrency 6 \
  --reasoning-effort medium --max-output-tokens 8192 \
  --model-timeout 120 --timeout 300 --total-token-limit 100000 \
  --output evals/model_tools/outputs/comparison
```

That configuration schedules 384 trials: 16 tasks, three surfaces, two models,
two phrasings, and two repetitions. Jobs are shuffled reproducibly. Output
directories must be new, preventing accidental overwrite or mixing of trials.
The runner enforces time, model-request, tool-call, and total-token limits per
trial. It never enables real domain effects: approvals and operations are
synthetic fixtures. Model API requests still use the configured paid services.

For a small check or a description comparison:

```bash
uv run --no-sync python -m evals.model_tools.run \
  --models openai:gpt-5.6-luna --cases assignment collision rejection \
  --repetitions 1 --phrasings 1 \
  --reasoning-effort medium --max-output-tokens 8192 \
  --model-timeout 120 --timeout 300 --total-token-limit 100000 \
  --output evals/model_tools/outputs/smoke

uv run --no-sync python -m evals.model_tools.run \
  --models openai:gpt-5.6-luna openai:gpt-5.6-sol \
  --cases assignment mutation collision nested return continue discovery opaque \
  --guidance short examples --repetitions 2 --phrasings 1 \
  --reasoning-effort medium --max-output-tokens 8192 \
  --model-timeout 120 --timeout 300 --total-token-limit 100000 \
  --output evals/model_tools/outputs/descriptions
```

Each run writes configuration, source snapshots and hashes, append-only synthetic trial
records, and a progressively updated Markdown summary. Records contain the
actual prompts, tool schemas, tool observations, completion, effects, failures,
usage, and elapsed time. They do not contain credentials. Unexpected external
exceptions are recorded by type to avoid persisting request headers or bodies.
Non-JSON runtime values are recorded as type markers rather than crashing the
trace writer or invoking arbitrary representations.

The GPT-5.6 commands explicitly fix reasoning effort and raise token/time limits
to accommodate reasoning models. Both models and all surfaces use identical
limits. Historical GPT-4.1 runs used the runner's legacy defaults: 2,500 output
tokens per response, 30-second model timeout, 120-second trial timeout, and
45,000 total tokens per trial. They are not a controlled model-only comparison
with the GPT-5.6 runs. Usage details include provider-reported reasoning counts;
response model names and finish reasons are retained when the agent returns a
result. Terminal exceptions can leave that response metadata unavailable.

## Interpreting results

`passed` includes the control result. `state_passed` excludes only the
wrong-control-kind failure, isolating value/effect handling. A correct output
with an unintended early return is still an overall failure. Error totals include
deliberately requested error scenarios. Required one-time domain calls cannot be
skipped or replaced with a guessed literal answer.

Tool count is a diagnostic, not the success criterion: different valid
strategies may make different numbers of inspection/evaluation calls. Duplicate
domain calls are scored separately because they may duplicate effects. Request
and token usage include failures. Trial-level Wilson intervals are descriptive;
repeated formulations of the same task are correlated, so those intervals do
not establish statistically independent population performance.

The reported operation count covers calls admitted to the workspace. Pydantic
AI's structured completion tool and schema-rejected requests before entering
the workspace are not included. Observed error totals include deliberately
requested errors; the raw `unexpected_tool_errors` field is only an allowance-
adjusted diagnostic, not a complete classification of all model request errors.
Model token usage and overall outcomes remain inclusive of those interactions.

The nested fixture calls a deterministic async child ten times and checks the
materialized parent counter. It does not launch ten additional LLM agents.
Approval fixtures await in-process and cover rejection/partial effects; they do
not implement a durable ledger or the production lifecycle. Unit tests separately
cover cancellation while waiting for approval. Binding types are supplied by
fixtures; Python source capture and initial-value type inference are outside
this prototype. Backend-specific transport behavior requires separate runs.

The small, development-visible task set is diagnostic, not a held-out benchmark.
Do not claim a universally optimal tool count or production readiness from it.

See the [GPT-5.6 assessment](evidence/20260909-gpt56-assessment.md) for the requested
Luna/Sol comparisons and the [historical assessment](evidence/20260909-assessment.md)
for the earlier GPT-4.1 experiments. Each includes separate studies, reviewable
metrics, limitations, and design interpretations.
The [initial tool-design review](evidence/20260908-tool-design-review.md) preserves
the pre-experiment rationale formerly embedded in the Ideal State.
Regenerate a detailed report from a complete local run with:

```bash
uv run --no-sync python -m evals.model_tools.report \
  evals/model_tools/outputs/comparison --output /tmp/model-tools-report.md
```
