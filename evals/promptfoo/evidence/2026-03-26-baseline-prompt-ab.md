---
eval_id: eval-uL6-2026-03-26T13:47:22
config: promptfooconfig-prompt-ab.yaml
date: 2026-03-26
decision: Establish prompt A/B baseline; all variants match or exceed control
---

## Providers tested

- control: eval_default + examples (openai-responses:gpt-5.4-mini)
- eval_sequenced + examples (openai-responses:gpt-5.4-mini)
- eval_mutation_aware + examples (openai-responses:gpt-5.4-mini)
- eval_default + mutation_hint (openai-responses:gpt-5.4-mini)

## Results summary

| Variant | Pass | Fail | Error | Score | Latency |
|---------|------|------|-------|-------|---------|
| control: eval_default + examples | 51 | 1 | 0 | 51.75 | 156,983ms |
| eval_sequenced + examples | 52 | 0 | 0 | 52.00 | 133,956ms |
| eval_mutation_aware + examples | 52 | 0 | 0 | 52.00 | 136,005ms |
| eval_default + mutation_hint | 52 | 0 | 0 | 52.00 | 140,615ms |

52 test cases per variant.

## Failures

- **control only**: P-TOOL-012 (nh_eval to remove negative numbers) — expected `[1, 3, 5]`, got `[1, -2, 3, -4, 5]`. Known flaky (mutation vs filter ambiguity).

## Decision rationale

Baseline established. All non-control variants achieved 52/52. Control's single failure (P-TOOL-012) is a known flaky test case. Results are consistent across both baseline runs (initial and updated).
