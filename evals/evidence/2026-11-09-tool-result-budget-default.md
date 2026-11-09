# Tool result budget default

## Decision and scope

The default `StepContextLimits.tool_result_max_tokens` is 4,000 tokens instead of 1,200.
Tool results that exceed the budget remain readable through the retained-result paths recorded in [Retained tool results](2026-11-08-tool-result-retention.md).
The system prompt and every other limit are unchanged.

The focused evidence for the budget is in that record: on the three cases whose results exceed the budget, 4,000 used fewer requests than 1,200 for both models (36 against 50 and 63).
This record adds the check that the complete suite keeps its results at 4,000.

## Evaluation protocol

Both arms ran the complete model-evaluation suite (64 cases) once per model on the same source.

| Arm | Source | `tool_result_max_tokens` |
| --- | --- | ---: |
| 1,200 | `bfaa980` (source fingerprint `b57db37092c75a91`) | 1,200 (the default before this change) |
| 4,000 | `bfaa980` | 4,000, through `--evaluation-context-limits` |

- Models: `openai-responses:gpt-6-luna` and `openai-responses:gpt-6.1-sol`; every other setting was the production default.
- Versions: Python 3.13.12, Pydantic 2.14.2, Pydantic AI Slim 2.63.0, OpenAI SDK 3.41.0, pytest 9.2.0.
- The two arms ran concurrently from 2026-11-09 09:32 UTC, for 709 seconds (1,200) and 800 seconds (4,000).

## Results

Completed-agent usage for the complete suite:

| Arm | Model | Passed | Requests | Input tokens | Cache-read input tokens | Output tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1,200 | Luna | 64/64 | 213 | 233,631 | 87,034 | 9,682 |
| 1,200 | Sol | 64/64 | 209 | 246,098 | 94,869 | 5,639 |
| 4,000 | Luna | 64/64 | 202 | 244,562 | 90,970 | 8,509 |
| 4,000 | Sol | 64/64 | 202 | 222,382 | 70,424 | 5,415 |

- Omission markers appeared in the same four cases per model in both arms: the three cases whose results exceed the budget and `test_truncated_input_preserves_full_value_and_identity`, whose marker comes from an input value rather than a tool result.
  No other case produced an omission marker, so no other tool result exceeded 1,200 tokens and the budget did not change their prompts.
- On the three cases whose results exceed the budget, Sol used 12 requests and 54,092 input tokens instead of 21 and 79,504; each case took four requests instead of seven.
  Luna used 14 requests instead of 15 and 68,057 input tokens instead of 34,963, because each result carried more of the source text.
- The remaining usage differences are in cases without omission markers, such as one Luna structured-output trial that used 15 requests at 1,200 and 4 at 4,000; one trial per case does not separate them from run-to-run variation.

## Interpretation and limits

The larger default reduces reads of omitted parts when a tool result is a few thousand tokens, and it does not change results that already fit.
Each result can now add up to 4,000 tokens to every later request of the same step, which is still small against provider per-request limits.
The cases are synthetic, and one trial per case and model does not establish reliability or a general optimum.
Usage is completed-agent usage, not a billing record.
The arms ran concurrently, so elapsed times compare only roughly.

## Artifacts and verification

Run directories under ignored `evals/outputs/`: `20261109-tool-result-budget-1200-shared` and `20261109-tool-result-budget-4000-shared`.
