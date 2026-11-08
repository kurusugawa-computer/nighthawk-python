# Retained tool results

## Decision and scope

Tool results are bounded by `tool_result_max_tokens`, and omission markers state the expression that reads the omitted part.
Before this change, that expression existed only when an `nh_evaluate` expression was itself a path, such as `request` or `o.b[0]`.
For a helper call, a native tool, or a coding-agent tool call, the marker stated only the amount, such as `<+18397 characters>`, so the omitted part could be obtained only by repeating the call.

A successful result that is not read from a name is now rendered under the step's next retained-result path, such as `nh_results[0]`.
When the rendering names that path, through an omission marker or a reference to an object shown earlier in the same result, the result stays readable as the same live object until the step ends, and the marker reads, for example, `<+18397 characters; nh_results[0][5167:]>`.
The reserved name avoids application names, cannot be assigned, and does not appear in LOCALS, discovery, outputs, or other steps.
The default system prompt and `tool_result_max_tokens` (1,200) are unchanged.

## Evaluation protocol

Three cases were added to `tests/integration/test_model_behavior.py`.
Each source can be obtained only once: a later call returns an empty string or an empty list.

- `test_drained_text_is_read_beyond_the_tool_result_budget`: a helper returns a notice of about 5,300 tokens; the requested code appears after about 4,700 tokens, and an expired code appears near the beginning.
- `test_drained_records_are_read_beyond_the_tool_result_budget`: a helper returns 240 event records (about 7,200 tokens in Python notation); the one refund request, at index 205, does not contain the word "refund".
- `test_drained_native_tool_result_is_read_beyond_the_tool_result_budget`: a native tool declared with `nh.scope(tools=...)` returns a manual of about 5,300 tokens; the rear axle torque appears after about 4,900 tokens, and the front axle torque appears near the beginning.

Arms:

| Arm | Source | `tool_result_max_tokens` |
| --- | --- | ---: |
| Unbounded | `dd185c5` (source fingerprint `1ded318646ceedb7`) | 1,000,000 |
| Retained 1,200 | `dd185c5` | 1,200 (default) |
| Retained 4,000 | `dd185c5` | 4,000 |
| Control 1,200 | `184c255` (fingerprint `90cf200bc6ef14fe`): revision `2541767` with the case commit `dd185c5` cherry-picked, without retention | 1,200 (default) |

The unbounded arm reproduces the earlier provider-backed tool loop, which sent successful results in full.
`--evaluation-context-limits` selected the budget; every other setting was the production default.

- Each case ran three times per model in every arm.
- Models: `openai-responses:gpt-6-luna` and `openai-responses:gpt-6.1-sol`.
- Versions: Python 3.13.12, Pydantic 2.14.2, Pydantic AI Slim 2.63.0, OpenAI SDK 3.41.0, pytest 9.2.0.
- The four arms ran concurrently from 2026-11-08 18:17 to 18:22 UTC.
- The complete model-evaluation suite (the 61 earlier cases and the three new cases) then ran once per model on `dd185c5` with the default budget, from 18:18 to 18:31 UTC, overlapping the focused runs.

## Results

Completed-agent usage for the three cases (three trials per case and model):

| Arm | Model | Passed | Requests | Input tokens | Cache-read input tokens | Output tokens | Trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Unbounded | Luna | 9/9 | 27 | 127,209 | 70,873 | 956 | 60.0 |
| Unbounded | Sol | 9/9 | 27 | 126,753 | 98,531 | 630 | 67.8 |
| Retained 1,200 | Luna | 9/9 | 50 | 117,484 | 83,209 | 3,392 | 110.2 |
| Retained 1,200 | Sol | 9/9 | 63 | 241,912 | 191,754 | 1,744 | 133.3 |
| Retained 4,000 | Luna | 8/9 | 36 | 148,034 | 100,721 | 2,184 | 79.6 |
| Retained 4,000 | Sol | 9/9 | 36 | 170,046 | 140,531 | 852 | 79.0 |
| Control 1,200 | Luna | 6/9 | 48 | 110,284 | 75,675 | 4,642 | 122.8 |
| Control 1,200 | Sol | 6/9 | 70 | 215,521 | 171,600 | 2,512 | 161.2 |

Native tool case:

- Both retained arms passed 12 of 12 trials.
  Every trial called the tool once and read the rest of the manual through `nh_results[0]`, in 9 of 12 trials with the marker expression or a slice from its offset, such as `nh_results[0][5167:]`.
- The control passed 0 of 6 trials.
  Luna answered without the specification in all three trials: 140 (the front axle value shown before the cut) once and 160 twice, and one trial called the tool again and received an empty string.
  Sol raised in all three trials, stating that the manual was cut before the specification.
  Five of the six control trials searched `globals()`, `locals()`, or the runtime environment for a retained copy before answering or raising.
- The unbounded arm passed 6 of 6 trials with one tool call and one assignment.

Helper-call cases:

- In 47 of the 48 helper-call trials across the arms, the model first assigned the helper result to a new name with `nh_assign` and then read that name; the exception is the Luna failure below.
  Retention therefore did not change these cases; the budget bounded the assignment summaries and later reads.
- Sol at 1,200 read the text and the records in slices of about 50 records or 5,500 characters, each bounded again, which accounts for most of its 63 requests.
  At 4,000, Sol read the rest in one expression (`notice[19968:]`, `events[131:]`).
- The one Luna failure at 4,000 evaluated `next(... if 'refund' in event['note'] ...)` directly on the helper call as its first action, found no match, and raised; the trial never displayed the records.
  Every other Luna trial of this case in the bounded arms began with the same keyword search on the assigned records and recovered after it found nothing.

Complete suite on the retained 1,200 arm:

- All 128 trials passed (64 cases per model).
- No trial of the 61 earlier cases produced a retained-result marker, so retention did not change their prompts or results.
  Their completed-agent usage was 190 requests and 182,117 input tokens for Luna and 188 requests and 166,832 input tokens for Sol.

## Interpretation and limits

Retention makes an omitted part of a result readable when the model did not keep the result itself, which the native tool case requires; without it, the bounded result lost the specification and Luna answered with unsupported values.
The budget trades result size against read requests.
On these cases, 4,000 used fewer requests than 1,200 for both models (36 against 50 and 63), less input for Sol (170,046 against 241,912), and more input for Luna (148,034 against 117,484).
The unbounded arm used the fewest requests because each result was 5,300 to 7,200 tokens; it places no limit on a single result, which can reach the provider's per-request input limit.

The cases are synthetic; three trials per case and model do not establish reliability or a general optimum for the budget.
Usage is completed-agent usage, not a billing record.
The arms ran concurrently, so the trial seconds compare only roughly.

## Artifacts and verification

Run directories under ignored `evals/outputs/`:

- `20261108-tool-result-retention-unlimited-focused`, `20261108-tool-result-retention-retained-1200-focused`, `20261108-tool-result-retention-retained-4000-focused`, and `20261108-tool-result-retention-retained-1200-shared`.
- `20261108-tool-result-retention-control-1200-focused` (from the control worktree).

Deterministic coverage is in `tests/core/test_environment.py`, `tests/adapters/pydantic_ai/tools/test_execution.py`, and `tests/adapters/pydantic_ai/models/test_tool_bridge.py`.
