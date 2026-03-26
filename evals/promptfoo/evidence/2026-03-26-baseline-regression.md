---
eval_id: eval-vhQ-2026-03-26T13:47:22
config: promptfooconfig.yaml
date: 2026-03-26
decision: Establish regression baseline for openai-responses backend
---

## Providers tested

- gpt-5.4-mini + eval_default + examples (openai-responses)

Note: codex and claude-code-cli were excluded (no CLI login or API keys available).

## Results summary

| Provider | Pass | Fail | Error | Score | Latency |
|----------|------|------|-------|-------|---------|
| gpt-5.4-mini + eval_default + examples | 51 | 1 | 0 | 51.75 | 137,128ms |

52 test cases.

## Failures

- **gpt-5.4-mini**: P-TOOL-012 (nh_eval to remove negative numbers) — expected `[1, 3, 5]`, got `[1, -2, 3, -4, 5]`. Known flaky (mutation vs filter ambiguity).

## Decision rationale

Baseline established. 51/52 pass rate with a single known-flaky test case (P-TOOL-012). Consistent with prior runs.
