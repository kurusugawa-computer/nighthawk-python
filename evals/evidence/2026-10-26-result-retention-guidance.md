# Guidance for retaining Python function results

## Decision

Clarify the standard execution prompt's distinction between assignment and evaluation.
Use `nh_assign(target_path, expression)` when the actual Python value must be retained, including a function's returned object.
The expression runs once and its value is stored directly.
Use `nh_evaluate(expression)` for inspection or effects whose result need not be saved; it creates no last-result binding.
Remove the overlapping note about preserving intermediate state.

The earlier prompt directed function calls to evaluation without distinguishing calls whose returned object must be retained.
Observed failures discarded an opaque result, tried to retrieve it through a nonexistent `_` binding, or called the helper again while assigning.
The correction describes existing operations; it adds no automatic result handles, retries, tools, or runtime behavior.

## Controlled comparison

The unchanged `test_python_call_preserves_identity_and_effects` cases exercise synchronous and asynchronous Python functions.
They check the committed object by identity and require exactly one actual helper call with the requested arguments.
Each of two models ran both cases three times with fresh state and `{"openai_reasoning_effort":"low"}`.
Only the standard execution prompt changed between control and treatment; cases, assertions, tools, model settings, and runtime mechanisms stayed the same.

| Prompt | Model | Passed | Assertion failures | Observed requests | Input tokens | Output tokens | Sum of trial seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Control | `openai-responses:gpt-6-luna` | 3 | 3 | 18 | 16,952 | 877 | 31.822 |
| Control | `openai-responses:gpt-6.1-sol` | 6 | 0 | 12 | 9,687 | 321 | 24.129 |
| Treatment | `openai-responses:gpt-6-luna` | 6 | 0 | 12 | 9,663 | 321 | 19.037 |
| Treatment | `openai-responses:gpt-6.1-sol` | 6 | 0 | 12 | 9,663 | 321 | 26.298 |

All 12 trials completed in each phase, without execution errors, skips, or success-seeking retries.
The control's Luna failures were one duplicate synchronous call (`trial-0001.json`), one missing asynchronous result (`trial-0004.json`), and one missing synchronous result (`trial-0005.json`).
The treatment passed all 12 trials, retaining the original objects with one helper call each.
Pytest reported 56.04 seconds for the control and 45.42 seconds for the treatment.
The control overlapped the end of the preceding schema evaluation; these times are not isolated performance measurements.

```bash
uv run --no-sync --env-file .env pytest -q --tb=short \
  tests/integration/test_model_behavior.py -k python_call_preserves_identity \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-model openai-responses:gpt-6.1-sol \
  --evaluation-repeat 3 \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/20261026-result-retention-treatment
```

## Shared and provider verification

The final prompt ran all 35 shared cases once for each model, preserving their low reasoning setting and all acceptance criteria.
Both models passed all 35 cases, including exception selection, object identity, effects, loop control, and actual Natural parent/child compositions.

| Model | Passed | Observed requests | Input tokens | Output tokens | Sum of trial seconds |
| --- | --- | --- | --- | --- | --- |
| `openai-responses:gpt-6-luna` | 35 | 124 | 121,353 | 3,582 | 206.165 |
| `openai-responses:gpt-6.1-sol` | 35 | 123 | 115,103 | 3,149 | 269.568 |

The same pytest invocation enabled the 12 separate OpenAI integration cases, preserving each case's own models and settings.
All 12 passed, including native image input and image tool results, in 84.370 summed case seconds.
The complete invocation reported 82 passed and 12 disabled coding-agent cases skipped in 560.34 seconds.
`integration.xml` retains those integration results alongside the 70 shared trial records.

```bash
NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 \
NIGHTHAWK_OPENAI_MULTIMODAL_INTEGRATION_TESTS=1 \
uv run --no-sync --env-file .env pytest -q --tb=short tests/integration/ \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-model openai-responses:gpt-6.1-sol \
  --evaluation-repeat 1 \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/20261026-result-retention-shared \
  --junitxml=evals/outputs/20261026-result-retention-shared/integration.xml
```

The image cases used unchanged programs and assertions, without adding an image-processing dependency.
Their success does not erase the earlier unavailable-`PIL` failure or isolate a causal fix for image interpretation.
One shared-suite repetition is a regression check, not an estimate of general reliability.

The final default suite passes with 1,324 passed and 64 skipped in 50.90 seconds.
Ruff format and lint, Pyright, strict documentation generation, and rendered internal links pass.

## Provenance and limits

The control ran at base revision `8f3e9fbd6983f99aea4f2155ae1ca94f98bf0f85` with the [exception-selection correction](2026-10-26-exception-binding-selection.md) in the working tree.
That correction was committed as `68c354648eec8fa7917b2cc574a7d0cca972eff1` before the prompt treatment.
Manifests record individual file hashes; each artifact directory retains an exact `sources.tar.gz`, trial records, and summary.

| Artifact directory under `evals/outputs/` | Source SHA256 |
| --- | --- |
| `20261026-result-retention-control` | `e96f5fd861efe6e5f09ecfaf3f364f2f1fa65a409eb848b419556dce806c1d72` |
| `20261026-result-retention-treatment` | `f056e338cb8be8d253fece42fc101b2618c9b3be052d56a577490b4b8d208642` |
| `20261026-result-retention-shared` | `f056e338cb8be8d253fece42fc101b2618c9b3be052d56a577490b4b8d208642` |

The environment uses Python 3.13.12, Nighthawk 0.17.0, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, OpenAI Python 3.35.1, MCP 2.3.0, and Claude Agent SDK 0.2.151.

Three repetitions support the observed improvement on these cases, not a guarantee that models preserve all objects or obey every requested call count.
Completed-agent usage can omit failed provider work and is not a billing record.
The broader prompt should continue to be assessed by task results and effects rather than by matching its wording.
