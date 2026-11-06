# Return operation failures to the model

## Decision

An ordinary exception raised while admitting or executing a Natural operation fails only that operation.
This covers a generated expression's own errors, application helpers and argument validators, native tool bodies, and uncaught child Natural failures.
The Pydantic AI executor returns the exception class and a bounded message to the model with the failed `operation_id`.
The model selects how the step ends: it can correct the cause, continue, finish, or select `raise` with `raise_operation_id` to raise the original exception unchanged.
Inspection failures, `AbortStep`, delivery failures, interruptions, and `TerminalOperationError` still end the step; the Harness's `PerimeterEffectRejectedError` is a `TerminalOperationError`.
The [operation inspection contract](../../docs/specification.md#103-operation-inspection) owns the exact behavior.

The previous contract propagated these exceptions to Python except for runtime syntax, name-resolution, and value-validation diagnostics.
In the [Session conversation record](2026-11-06-conversation-binding-names.md), expressions that raised on their own, such as awaiting a synchronous function or reading attributes of None, caused most remaining failures.

## Comparison protocol

Both arms add two shared model-evaluation cases to `tests/integration/test_model_behavior.py`:

| Case | Task | Assertion |
| --- | --- | --- |
| `test_failed_helper_call_is_retried_after_its_reported_failure` | `fetch_rate` raises `ConnectionError("rate service temporarily unavailable; calling again will succeed")` on its first call. | `rate == 1.25`, two calls with `"EUR"`, outcome `Pass()`. |
| `test_failed_helper_exception_propagates_unchanged_when_requested` | `charge` raises an application `PaymentDeclinedError`; the program asks to let the original exception propagate without retrying. | The same instance propagates with its attribute, and `charge` runs once. |

The control is the unpublished revision `7f52207`, which is `a73b5d2` plus only those two cases.
The treatment is the unpublished revision `e5ae13e` with the contract change and the same cases.
Each arm ran the complete shared suite, `pytest tests/integration/ -m model_evaluation --evaluation-model openai-responses:gpt-6-luna --evaluation-repeat 3`, with production defaults.
The two arms ran concurrently in separate worktrees.

The Session conversation case `tests/integration/harness/test_session.py::test_single_model_conversation_calculation_retention_and_function_call` ran 30 times on the treatment, as independent pytest processes six at a time.
Its control is the 30-trial treatment arm of the [Session conversation record](2026-11-06-conversation-binding-names.md), whose source equals `a73b5d2`.

## Results

| Arm | Shared suite | Existing 49 cases: requests / input / output tokens | New cases: passed |
| --- | --- | --- | --- |
| Control | 150 passed, 3 errors | 483 / 435,892 / 20,952 | Retry 0/3; propagation 3/3 |
| Treatment | 153 passed | 483 / 452,252 / 18,619 | Retry 3/3; propagation 3/3 |

All 49 existing case groups passed 3/3 in both arms.
The three control errors are the retry case, where the first `ConnectionError` propagated out of the step.
The fixture records no usage for those failed control trials.
Every treatment retry trial called `fetch_rate` twice and finished in three requests.
Every treatment propagation trial selected `raise_operation_id` in its second request, and the original instance propagated.
Control propagation trials passed because the exception propagated without a model decision.
Treatment input tokens on the existing cases rose 3.8%, consistent with the added `raise_operation_id` schema field; request counts were equal.
Call-phase time on the existing cases was 1,032 seconds for the control and 985 seconds for the treatment.

| Session conversation arm | Passed | Requests | Input tokens | Output tokens |
| --- | --- | --- | --- | --- |
| Control (`a73b5d2` source) | 28/30 | 545 | 1,206,376 | 36,496 |
| Treatment | 30/30 | 563 | 1,251,499 | 37,823 |

No treatment Session trial raised an exception from an operation, so these trials show no regression but do not exercise recovery.
Its recoverable errors were six name-resolution or syntax diagnostics and one output-target correction, which both contracts already returned to the model.

Three repetitions per case and 30 Session trials detect only large differences.
The new cases are synthetic; they show that the model can retry a reported transient failure and propagate an original exception on request, not that it chooses well for arbitrary application failures.
Exception messages reach the model provider under the treatment.
Codex, Claude, and other models were not run.

## Source and environment

The adopted revision differs from the evaluated treatment only in the transport of an application argument-validator exception: the evaluated treatment returned it as a Pydantic AI `ModelRetry`, and the adopted revision uses `ToolFailed`, which consumes no retry budget, including over coding-agent tool bridges.
No evaluated case uses an argument validator.

Python 3.13.12, Nighthawk 0.19.0, Pydantic 2.14.2, Pydantic AI 2.63.0, OpenAI 3.41.0, HTTPX 0.28.1, pytest 9.2.0.
The model-evaluation fixture's source digests are `5d22f70c94a94b7fd8c080f8aedf2b12acba4ac17056acd7ca3fbeb276454808` for the control and `970f3f13a9fa5197fb457d4ec58d789593b8d1d75b7997e35e4532caa9bda035` for the treatment.
