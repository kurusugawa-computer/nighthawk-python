# API consolidation regression check

## Decision and scope

This change consolidates the public API between revisions `9de2b0c` and `3fa8741`.
It replaces the scope getters with one `Scope` record, uses `StepCandidate` and `StepRequest` in deterministic test executors, moves executor-boundary types to `nighthawk.runtime`, removes unused runtime bookkeeping, and simplifies resilience and backend settings.
The default system prompt, user prompt template, tool definitions, tool docstrings, and model-facing feedback payloads are unchanged, so no prompt experiment was needed.
The live runs check that production execution still behaves as before; they do not evaluate a model-facing change.

## Protocol

All runs use `openai-responses:gpt-6-luna` with production defaults, no evaluation settings, and fresh fixture state for each trial.
Versions: Pydantic 2.14.2, Pydantic AI 2.63.0, OpenAI 3.41.0, pytest 9.2.0, pytest-asyncio 1.4.1.

1. The shared model evaluation suite runs all 50 cases once on the candidate `3fa8741`, source fingerprint `1150559e87f8…`.
2. Its only failing case, `test_explicit_task_source_survives_python_shadowing`, runs five times on the candidate `aae58d2`, source fingerprint `0c76fd3c93d1…`, and five times on the control `9de2b0c`, source fingerprint `ec72a3dc2b0f…`.
   The control runs from an immutable worktree of `9de2b0c` with the same locked environment.
3. The OpenAI integration tests run with `NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1`.

## Results

| Run | Passed | Failed | Error | Requests | Input tokens | Output tokens | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Shared suite, candidate | 49 | 1 | 0 | 170 | 187,950 | 7,709 | 287.7 |
| Shadowing case, candidate | 0 | 4 | 1 | 8 | 6,607 | 2,205 | 47.9 |
| Shadowing case, control | 1 | 3 | 1 | 16 | 28,833 | 3,006 | 78.7 |

Every failed shadowing trial on both sides commits the Python value 7 instead of the requested task value 100.
The one error on each side is an operation-execution failure during the step.
The [Pydantic AI 2.63 record](2026-10-30-pydantic-ai-2.63.md) reports the same failure for this case on the control source fingerprint.
The OpenAI integration tests pass: 10 passed, with coding-agent and opt-in evaluation cases skipped.

## Limitations

The shadowing case remains unreliable for `gpt-6-luna` on both implementations; five trials per side cannot distinguish 0/5 from 1/5.
The shared suite ran once, so it shows that the cases still execute end to end, not their pass rates.
Coding-agent backends were not run.
Synthetic success does not establish application-level judgment quality.

## Final revision check

Revision `3319e77`, source fingerprint `984eb9515c23…`, includes the [rejection feedback](2026-10-31-rejection-feedback-shape.md) and [skip guidance](2026-10-31-skip-guidance.md) changes.
The shared suite runs once with the same settings: 49 passed and 1 failed, using 162 requests, 138,639 input tokens, 7,697 output tokens, and 270.5 summed trial seconds.
The failure is again `test_explicit_task_source_survives_python_shadowing`, committing the Python value 7.
The OpenAI integration tests pass: 10 passed, with coding-agent and opt-in evaluation cases skipped.
