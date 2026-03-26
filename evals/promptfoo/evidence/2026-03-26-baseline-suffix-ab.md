---
eval_id: eval-yYN-2026-03-26T13:47:23
config: promptfooconfig-suffix-ab.yaml
date: 2026-03-26
decision: Establish suffix A/B baseline after decisional removal; examples suffix remains strongest
---

## Providers tested

- control (current suffix) — openai-responses:gpt-5.4-mini
- suffix: terse — openai-responses:gpt-5.4-mini
- suffix: examples — openai-responses:gpt-5.4-mini

All variants use eval_default.txt system prompt and eval_examples tool preset.

## Results summary

| Variant | Pass | Fail | Error | Score | Latency |
|---------|------|------|-------|-------|---------|
| control (current suffix) | 51 | 1 | 0 | 51.75 | 140,945ms |
| suffix: terse | 51 | 1 | 0 | 51.50 | 136,767ms |
| suffix: examples | 52 | 0 | 0 | 52.00 | 141,921ms |

52 test cases per variant.

## Failures

- **control**: P-TOOL-012 (nh_eval to remove negative numbers) — expected `[1, 3, 5]`, got `[1, -2, 3, -4, 5]`. Known flaky.
- **terse**: P-TOOL-012 — outcome_kind was `raise` instead of `pass`, and binding mismatch. Score 51.50 (2 assertion failures on 1 test case).

## Decision rationale

Baseline established post-decisional removal. The examples suffix variant achieved 52/52, the only variant with a perfect score. Terse scored lowest (51.50) due to a raise outcome on P-TOOL-012, a distinct failure mode from the mutation-vs-filter flake seen in control. Results confirm examples as the strongest suffix variant.

## Rejected variants

- **suffix: decisional**: Removed from eval config prior to this run. Across 3 prior runs (v1, v2, initial baseline), decisional never outperformed control. In old runs it was the clear worst performer (score 40.25 with 10 errors in v1, 44.75 with 6 errors in v2, vs control 46.00 in both). No re-test value.
