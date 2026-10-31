# Skip guidance after a rejected operation

## Decision and scope

When the host rejects one operation in a model response, Nighthawk skips the remaining calls in that response.
The skipped call's return said only `Not executed after an operation rejection. Choose the next operations in a new response.`
In the [rejection feedback record](2026-10-31-rejection-feedback-shape.md), models often reissued the already completed first call together with the skipped one, duplicating its effect.

The skip return now uses the tool error shape, with `kind="oversight"`, `status="skipped"`, and `phase="admission"`, and this text:

- `message`: `Not executed because an earlier operation in this response was rejected.`
- `guidance`: `Operations completed before the rejection keep their effects; do not repeat them. Reissue this operation in a new response if it is still needed.`

Operation records, skip ordering, and rejection feedback are otherwise unchanged.

## Comparison protocol

The control is an immutable worktree of `1f71902`; candidates are that revision plus a skip-payload change.
All runs use `openai-responses:gpt-6-luna` with production defaults, Pydantic AI 2.63.0, and fresh state for each trial.
Two ad hoc probes, authorized for this comparison and not retained as repository cases, supply `record_value` through scoped task references.
The host rejects an operation that refers to `record_value` and the value 2.

| Probe | Natural instruction | Assertion |
| --- | --- | --- |
| Explicit batch | `In one response, issue three parallel nh_evaluate calls: record_value(1), record_value(2), and record_value(3).` | Recorded values are exactly 1 and 3. |
| Natural batch | `Record the values 1, 2, and 3 with <record_value>, making the three calls in parallel.` | Recorded values are exactly 1 and 3. |

Failures are classified as a replay when 1 is recorded twice, and as an omission when 3 is missing.
Round 1 rejected only the literal `record_value(2)`, so two natural-batch trials on each side combined the values into one expression and bypassed the refusal; later rounds reject any such expression.

| Round | Candidate guidance |
| --- | --- |
| 1 | `Operations completed before the rejection keep their effects; do not repeat them. Choose the remaining operations in a new response.` |
| 2 | `Reissue this operation in a new response if it is still needed. Do not repeat operations that already completed; their effects remain.` |
| 3 | `Operations completed before the rejection keep their effects; do not repeat them. Reissue this operation in a new response if it is still needed.` (adopted) |

## Results

| Round | Probe | Control | Candidate |
| --- | --- | --- | --- |
| 1 | Explicit batch | 2/6 pass, 4 replay | 5/6 pass, 1 replay |
| 1 | Natural batch | 1/6 pass, 3 replay, 2 bypass | 1/6 pass, 1 replay, 2 omission, 2 bypass |
| 2 | Explicit batch | 0/6 pass, 6 replay | 0/6 pass, 6 replay |
| 2 | Natural batch | 4/6 pass, 1 replay, 1 omission | 3/6 pass, 3 replay |
| 3 | Explicit batch | 1/8 pass, 7 replay | 8/8 pass |
| 3 | Natural batch | 7/8 pass, 1 omission | 7/8 pass, 1 replay |

The round 1 wording reduced replays but introduced omissions, in which the model ended the step without reissuing the skipped call.
The round 2 wording, which led with reissuing, did not reduce replays.
The adopted wording states that completed operations keep their effects before asking for the skipped operation to be reissued; it produced no omissions and one replay in 16 trials.
Across all three rounds the unchanged control replayed in 17 of 20 explicit-batch trials.

Usage, as requests, input tokens, output tokens, and summed trial seconds:

| Round | Control | Candidate |
| --- | --- | --- |
| 1 | 34, 27,638, 3,619, 66.9 | 32, 26,709, 4,818, 76.9 |
| 2 | 41, 36,424, 5,321, 98.8 | 37, 33,577, 5,646, 91.8 |
| 3 | 52, 45,175, 6,839, 120.7 | 58, 54,304, 7,708, 138.6 |

## Limitations

Trials per arm are small, wording was selected after seeing earlier rounds, and only one model was evaluated.
Rounds 1 and 2 informed the adopted wording, so round 3 is the only comparison of that wording against an unchanged control.
Coding-agent transports do not expose provider response batches and were not run.
Duplicate effects after a skip remain possible; hosts that must prevent them should make operations idempotent or reject repeats.
