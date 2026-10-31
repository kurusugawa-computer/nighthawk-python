# One rejection feedback payload

## Decision and scope

Operation rejections reached the model in two shapes.
Tool denials used the tool error shape with `kind`, `message`, and `phase`, while output retries and ordered-batch denied returns used `OperationRejection.model_feedback()` with `reason`.
`model_feedback()` now returns the tool error shape, and tool denials reuse it:
`kind="oversight"`, `status="rejected"`, operation and step identities, the reason as `message`, `guidance`, `phase="inspection"`, and `evaluation_started=False`.
Tool denial text is unchanged; output retries and ordered-batch denied returns gain `kind` and `phase` and rename `reason` to `message`.

## Comparison protocol

The control is an immutable worktree of `ba101d0`; the candidate is that revision plus this change.
Both run `openai-responses:gpt-6-luna` with production defaults, Pydantic AI 2.63.0, and fresh state for each trial.
Three ad hoc probes, authorized for this comparison and not retained as repository cases, run five times on each implementation.
Helpers reach the Natural functions through scoped task references, and refusals come from `Oversight.inspect_operation`.

| Probe | Task and assertions |
| --- | --- |
| Return refusal | Return `legacy_total()` directly; the host refuses expressions mentioning it with guidance to return `audited_total()`. The refused helper never runs and 42 is returned. |
| Tool refusal | Send a summary to `'public'` with `send_report`; the host refuses with guidance to use `'internal'`. Only `'internal'` receives the report. |
| Batch skip | Issue `record_value(1)`, `record_value(2)`, and `record_value(3)` in one parallel response; the host refuses 2. The recorded values are exactly 1 and 3. |

## Results

| Probe | Control | Candidate |
| --- | ---: | ---: |
| Return refusal | 5/5 | 5/5 |
| Tool refusal | 5/5 | 5/5 |
| Batch skip | 1/5 | 1/5 |

Every return refusal records a rejected `return` operation followed by a successful one, and every tool refusal a rejected `evaluation` followed by a successful one.
Every batch-skip trial records `succeeded`, `rejected`, and `skipped` for the first response on both implementations.
In four trials on each side the model then reissues the already completed `record_value(1)` together with the skipped call, so 1 is recorded twice.
That behavior follows the skip feedback, which is unchanged, and it is identical across implementations.

Usage is 40 requests, 29,429 input tokens, and 3,531 output tokens for the control, and 40 requests, 29,654 input tokens, and 3,538 output tokens for the candidate.
Summed trial time is 100.4 seconds for the control and 97.7 seconds for the candidate.

## Limitations

Five trials per probe and one model do not establish general reliability.
Coding-agent transports, which already used the tool error shape, were not rerun.
The batch-skip replay of a completed call is a separate observation about skip guidance; it is not addressed here.
