# Explicit value descriptions

## Decision and implementation

This change implements the approved P-INJECT-001A and P-INJECT-002A decisions against revision `d6d5eeb`.
The [preceding context-guidance evaluation](2026-10-29-context-guidance.md) remains a historical record of the previous implementation.

The optional ordinary function is now `nighthawk.runtime.describe_value`.
Its first documentation line explains that the result is bounded metadata containing type, members, and preview.
The generated runtime root no longer exposes `.inspect`, and the default user prompt no longer prepends the inspection instruction.
No function is automatically added to locals or globals, no short-name allocator is introduced, and no new injection API is added.
Programs and hosts use ordinary Python references, explicit embedded inputs, or existing scoped task references.
Supplied functions retain the host-selected name and use ordinary callable rendering and expression evaluation.
Continuation and recovery guidance does not assume a particular binding name, so explicit aliases work too.

This is a public API change: callers migrate from `inspect_value` or the generated root's `.inspect` to an explicit `describe_value` binding.
The changelog, API reference, specification, usage guide, and five rendered prompt examples are updated together.
The generated runtime root remains available for environment discovery when context entries are omitted.
Named output contracts, source references, Python lookup, assignment admission, output validation, and native-media guidance retain their existing contracts.

Deterministic coverage exercises local, global, and task bindings under an explicit alias, callable presentation, ordinary unbound-local behavior, and operation lifecycle accounting.
Existing metadata, descriptor, annotation, pagination, and response-budget tests now use the renamed public function.

## Comparison protocol

The control is an immutable source snapshot of `d6d5eeb` using the same installed dependencies as the candidate.
All 47 pre-existing shared model cases run once per model, followed by two additional repetitions of seven selected cases, for 122 planned trials per implementation.
All shared integration test files are byte-identical between implementations.
No acceptance assertion, Natural program, model setting, or output validator is relaxed.

Models are `openai-responses:gpt-6-luna` and `openai-responses:gpt-6.1-sol`, with `{"openai_reasoning_effort":"low"}`.
The main command is:

```bash
uv run --no-sync --env-file .env pytest -q --tb=short tests/integration/ -m model_evaluation \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-model openai-responses:gpt-6.1-sol \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/<new-directory>
```

The candidate main batch excludes the new `tests/integration/test_value_description.py` with `--ignore`; that file has its own comparison below.
The repeated batch adds `--evaluation-repeat 2` and:

```text
-k 'python_call_preserves or output_contract_guides or truncated_input or explicit_task_source or application_inspect'
```

These cases cover synchronous and asynchronous calls, visible and omitted output contracts, truncated values, explicit shadowed-source selection, and an application function named `inspect`.
Repetitions have a fixed size; failed trials remain recorded.

After a candidate Luna trial omitted a required claim review, an additional paired batch runs that one composition case twice per model and implementation.
The resulting comparison contains 126 trials per implementation.
This diagnostic batch was selected after observing a failure, rather than being part of the original matrix.

## Shared-case results

| Cases | Control | Candidate |
| --- | ---: | ---: |
| All shared trials, including the diagnostic batch | 122/126 | 121/126 |
| Ordinary execution, excluding explicit shadowed-source selection | 119/120 | 118/120 |
| Review-policy composition, included in ordinary execution | 5/6 | 4/6 |
| Explicit selection of a shadowed task binding | 3/6 | 3/6 |

All failures are Luna trials.
In the source-selection case, Luna chooses the effective Python value `7` instead of the explicitly requested hidden task value `100` in all three trials of both implementations.
Sol passes all three source-selection trials in each implementation.

In the review-policy composition, Luna replaces semantic selection with a Python keyword/digit heuristic and omits the claim beginning with `Twenty`.
This occurs once in the control and twice in the candidate across three Luna trials per implementation; Sol passes all three trials in each implementation.
The failure occurs with both prompts despite the unchanged system instruction against replacing semantic judgment with keyword matching.
The extra candidate failure remains an observed regression; this sample does not establish that removing inspection guidance causes it, or establish quality equivalence.
No failure is repaired by weakening assertions or adding task-specific prompt instructions.

Completed-agent usage for all 126 trials per implementation:

| Variant | Model | Passed | Requests | Input tokens | Output tokens | Trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | Luna | 59/63 | 202 | 161,545 | 5,910 | 332.80 |
| Candidate | Luna | 58/63 | 198 | 172,662 | 5,890 | 316.76 |
| Control | Sol | 63/63 | 226 | 182,189 | 5,830 | 700.09 |
| Candidate | Sol | 63/63 | 223 | 172,674 | 5,710 | 631.42 |

Total input tokens increase from 343,734 to 345,336, or 0.5%.
Requests decrease from 428 to 421, output tokens from 11,740 to 11,600, and summed trial time from 1,032.89 to 948.18 seconds.
For ordinary trials alone, input tokens increase from 325,029 to 326,625, also 0.5%; requests decrease from 407 to 397.
These aggregates include incomplete work in failed trials, so they do not establish a resource improvement for equivalent successful work.

For the 94 paired first root user prompts, removing the old inspection prefix yields the candidate text exactly; all remaining characters match.
With `o200k_base`, every prompt is 25 tokens shorter.
The aggregate decreases from 10,626 to 8,276 tokens, or 22.1%.
The representative integer prompt decreases from 94 to 69 tokens.
These are fixed initial-user-prompt savings, not reductions in the entire provider request or whole-task input usage.

## Optional-helper comparison

The new case hides all object field and method details from the initial prompt.
Its task selects all public instance fields whose names end in `_count` and whose values are positive integers.
There are matching fields beyond the optional helper's default eight-member page, and a property that records and rejects any attempted read.
Assertions check all six selected names, normal completion, and absence of property reads.

Three conditions share the same program, input object, and acceptance criteria: ordinary builtins alone, an explicitly supplied standard-library `inspect` module, and explicitly supplied `describe_value`.
Both models run each condition three times, for 18 trials.
All 18 succeed, and every trial uses ordinary Python operations such as `vars(value)` instead of invoking either optional helper.
This establishes recovery for the tested instance-dictionary task without a dedicated inspection function.
It does not compare the quality of the two helper implementations when actually invoked, or establish that they are unnecessary for every object type.

| Available helpers | Passed | Requests | Input tokens | Output tokens |
| --- | ---: | ---: | ---: | ---: |
| Builtins alone | 6/6 | 15 | 10,036 | 601 |
| Explicit standard-library `inspect` | 6/6 | 15 | 10,280 | 638 |
| Explicit `describe_value` | 6/6 | 18 | 14,547 | 716 |

The additional calls in the last condition are Sol's ordinary Python inspection expressions, not calls to `describe_value`.
The result supports leaving optional helper selection to the program or host for this task; it does not measure the helper's pagination performance.

The first attempt at this additional comparison failed before Step execution because the new test passed `binding_name_to_type` to `execute_step_async` instead of `StepDefinition`.
All 18 fixture errors are retained in `helpers`; they contain no model requests.
After correcting the argument location, the complete fixed matrix runs in `helpers-corrected` without changing its program or acceptance criteria.

## Validation and limits

The deterministic suite passes 1,349 tests with 79 skipped in 50.45 seconds.
The existing OpenAI provider tests pass 12/12, with 12 coding-agent cases skipped, in 65.01 seconds.
Ruff lint and formatting, Pyright, strict documentation generation, and executable documentation examples pass.

These synthetic cases and small repeated samples do not establish general application reliability or measure cognitive load directly.
Dynamic token totals also reflect model-selected operation counts; fixed prompt savings and whole-trial usage must be assessed separately.
Completed-agent usage is not a billing record and can omit failed or interrupted provider requests.
Concurrent batches make elapsed times observations rather than an isolated latency benchmark.
Live coding-agent tests remain disabled; their deterministic adapters are covered by the full suite.

## Artifacts and reproducibility

Local artifacts use the ignored prefix `evals/outputs/20261029-explicit-description-`:

- `control`, `control-repeat`, and `control-composition` contain the baseline model records.
- `candidate`, `candidate-repeat`, and `candidate-composition` contain the changed implementation's records.
- `helpers` retains the initial fixture errors; `helpers-corrected` contains the 18 completed optional-helper trials.
- `comparison.json` contains aggregate usage, failures, paired initial prompts, helper-call expressions, and source fingerprints.
- `deterministic.xml` and `provider.xml` contain the test reports.
- `control-sources.tar.gz`, `candidate-sources.tar.gz`, and `final-sources.tar.gz` preserve the source and test files selected by the corresponding manifests.

The control source fingerprint is `cbbad06b21425ec711b5e76a3dd2054ca43cb1397e0dc29623ff8a85a25f1db5`.
The candidate main fingerprint is `265cd3733fb929dbd4c457ff12fd7e25ef69ea53c6f166e09ccbb260dbab99ac`.
Its archive includes the initial argument-location error in the new optional-helper test, which the main batch excludes.
After correcting that test, the final fingerprint is `9688e1143f1127e2b35491e8dbf052c40d88959da2f6dc78feede4aad418db0f`.
Production source and all shared integration tests are identical between the two candidate snapshots.
Archive members were checked against the recorded file hashes, and current source and tests match the final manifest.

Environment: Python 3.13.12, Nighthawk 0.17.0, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, OpenAI Python 3.35.1, MCP 2.3.0, and Claude Agent SDK 0.2.151.
