---
eval_id_prompt_ab: eval-b9Z-2026-04-27T13:16:51
eval_id_suffix_ab: eval-65u-2026-04-27T13:58:32
eval_id_nano_initial: eval-aFc-2026-04-27T16:04:44
eval_id_nano_in_place_hint: eval-SM4-2026-04-27T16:10:05
eval_id_final_nano: eval-YxZ-2026-04-27T16:13:52
eval_id_final_mini: eval-mgT-2026-04-27T16:18:26
config_prompt_ab: promptfooconfig-prompt-ab.yaml
config_suffix_ab: promptfooconfig-suffix-ab.yaml
config_nano: promptfooconfig-nano-sanity.yaml
config_final_mini: promptfooconfig.yaml --filter-providers "^gpt-5\.4-mini \+ eval_default \+ examples$"
date: 2026-04-27
decision: Keep legacy-style outcome semantics while shortening the always-on system prompt
---

## Providers tested

- legacy: eval_legacy_default + examples -- openai-responses:gpt-5.4-mini
- focused: eval_default + examples -- openai-responses:gpt-5.4-mini
- eval_mutation_aware + examples -- openai-responses:gpt-5.4-mini
- focused: eval_default + mutation_hint -- openai-responses:gpt-5.4-mini
- legacy suffix -- openai-responses:gpt-5.4-mini
- production suffix -- openai-responses:gpt-5.4-mini
- suffix: examples -- openai-responses:gpt-5.4-mini
- gpt-5.4-nano + eval_default + examples -- openai-responses:gpt-5.4-nano
- gpt-5.4-mini + eval_default + examples -- openai-responses:gpt-5.4-mini

## Results summary

Prompt A/B (`eval-b9Z-2026-04-27T13:16:51`):

| Variant | Pass | Fail | Error |
|---------|------|------|-------|
| focused: eval_default + examples | 50 | 2 | 0 |
| eval_mutation_aware + examples | 50 | 2 | 0 |
| focused: eval_default + mutation_hint | 51 | 1 | 0 |
| legacy: eval_legacy_default + examples | 49 | 3 | 0 |

Suffix A/B (`eval-65u-2026-04-27T13:58:32`):

| Variant | Pass | Fail | Error |
|---------|------|------|-------|
| legacy suffix | 52 | 0 | 0 |
| production suffix | 50 | 0 | 2 |
| suffix: examples | 51 | 1 | 0 |

Nano sanity progression:

| Eval | Variant | Pass | Fail | Error |
|------|---------|------|------|-------|
| eval-aFc-2026-04-27T16:04:44 | initial focused prompt | 49 | 3 | 0 |
| eval-SM4-2026-04-27T16:10:05 | in-place mutable-read hint | 51 | 1 | 0 |
| eval-YxZ-2026-04-27T16:13:52 | in-place hint + loop-control examples | 52 | 0 | 0 |

Final mini regression (`eval-mgT-2026-04-27T16:18:26`):

| Variant | Pass | Fail | Error |
|---------|------|------|-------|
| gpt-5.4-mini + eval_default + examples | 52 | 0 | 0 |

## Decision rationale

The focused default prompt removed always-on preview guidance and kept the trust boundary, tool-selection map, async hint, and large-state hint. That reduced the default prompt without weakening prompt-injection guidance or removing tool-choice few-shot signals.

The schema-semantic suffix candidate removed text that duplicated the JSON schema, including the explicit allowed-kind line and the bias toward `pass`. It regressed on outcome-kind cases (`P-OUTCOME-005`, `P-OUTCOME-006`) while the legacy-style suffix passed 52/52. The result suggests that the duplicated-looking outcome text is useful behavioral guidance for small models and should not be removed without stronger A/B evidence.

The first nano sanity run exposed three small-model regressions:

- `P-TOOL-012`: created or preserved a separate local instead of mutating the `data` list in place.
- `P-TOOL-051`: cleared a dict but did not perform the second requested mutation.
- `P-OUTCOME-014`: completed the mutation but output `pass` instead of the requested `continue`.

The final prompt keeps the shortened default prompt, adds one in-place mutable-read clarification, and adds loop-control examples only when `break` or `continue` is allowed. Production therefore uses the legacy-style suffix semantics, with two targeted fixes: `Default: pass.` is emitted only when `pass` is allowed, and loop-control guidance is injected only when loop outcomes are allowed.

## Rejected variants

- `terse` (schema-semantic suffix): regressed on `P-OUTCOME-005` / `P-OUTCOME-006` against the legacy-style suffix on gpt-5.4-mini. Removed from `evals/promptfoo/provider.py`; retain `legacy` for regression measurement and `examples` as the live A/B candidate.
