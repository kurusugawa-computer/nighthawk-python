# Async signatures and contextual discovery guidance

## Decision and scope

This change implements the approved P-ASYNC-001 and P-PREAMBLE-001 decisions against revision `8f3939d`.
The [preceding display evaluation](2026-10-29-python-context-display.md) records the earlier implementation and remains unchanged.

Async callables render as `Callable  # async (operation: str) -> str`, with any documentation after the signature.
Known async status is also retained when a signature cannot be displayed safely.
This changes the presentation order, not callable execution or automatic awaiting.

The default execute prompt removes these unconditional lines:

```text
Names and details: __nighthawk_runtime_0.environment.discover(), __nighthawk_runtime_0.environment.describe('name').
The runtime root cannot be rebound. Declared outputs are validated.
```

When a context section omits entries because of its item or token budget, its omission comment supplies both `environment.discover()` and `environment.describe('name')` using the live, collision-free runtime reference.
The existing inspection preamble and conditional native-media instruction remain.
Runtime-root protection, assignment admission, output validation, environment APIs, and control-result schemas are unchanged.

The five executable documentation prompt examples and the specification are updated with the implementation.
Deterministic coverage checks the async method's signature/documentation order and the absence of discovery guidance when no entries are omitted.
It also follows the advertised discovery pages and named-description expression to retrieve an omitted output contract when an application binding occupies `__nighthawk_runtime_0`.

## Comparison protocol

The control is an immutable source snapshot of `8f3939d`, with the same additional shared evaluation case as the candidate.
The new case restricts the locals summary to one entry, hiding the unbound output's `Literal['defer_review', 'page_on_call']` contract.
Its program asks for the appropriate contract value for a production outage without supplying either choice in program text or input data.
Assertions inspect the committed Python value and normal completion.
No previous evaluation program, assertion, model setting, or runtime validator is relaxed.

Both implementations run all 47 shared cases once per model, followed by two additional repetitions of six selected cases.
This produces 118 trials per implementation, with fresh fixture state for each trial.

Models and settings:

- `openai-responses:gpt-6-luna`
- `openai-responses:gpt-6.1-sol`
- `{"openai_reasoning_effort":"low"}`

The main command is:

```bash
uv run --no-sync --env-file .env pytest -q --tb=short tests/integration/ -m model_evaluation \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-model openai-responses:gpt-6.1-sol \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/<new-directory>
```

The repeated batch adds `--evaluation-repeat 2` and:

```text
-k 'python_call_preserves or output_contract_guides or truncated_input or explicit_task_source'
```

The six cases cover synchronous/asynchronous calls preserving identity and effects, visible and omitted output contracts, truncated values, and explicit selection of a shadowed task binding.
The control runs from its snapshot working directory with its source and test roots first on `sys.path`, using the same installed dependencies as the candidate.
Repeated batches have a fixed size; failures remain recorded instead of being retried until successful.

## Results

| Cases | Control | Candidate |
| --- | ---: | ---: |
| All shared trials | 117/118 | 115/118 |
| Ordinary execution, excluding explicit shadowed-source selection | 112/112 | 112/112 |
| Omitted output contract, included in ordinary execution | 6/6 | 6/6 |
| Explicit selection of a shadowed task binding | 5/6 | 3/6 |

Every omitted-contract trial in both implementations actually queries `environment.describe('selection')` before committing the expected value.
This establishes the tested recovery path with the guidance placed beside the omission instead of in the unconditional preamble.

All failures are Luna trials of `test_explicit_task_source_survives_python_shadowing`.
Luna selects the ordinary Python value `7` instead of the requested hidden task value `100` in one control trial and all three candidate trials.
Sol passes all three source-selection trials in both implementations.
The observed regression is retained in the results; the unconditional discovery line is not restored under the approved simplification.
The public discovery and explicit-source APIs remain available, with deterministic coverage of their behavior.

Completed-agent usage for the 112 ordinary trials:

| Variant | Model | Passed | Requests | Input tokens | Output tokens | Trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | Luna | 56/56 | 179 | 163,033 | 5,434 | 323.51 |
| Candidate | Luna | 56/56 | 156 | 114,941 | 4,374 | 253.69 |
| Control | Sol | 56/56 | 170 | 126,121 | 4,215 | 621.65 |
| Candidate | Sol | 56/56 | 174 | 123,127 | 4,299 | 562.41 |

Ordinary-trial input tokens decrease from 289,154 to 238,068, or 17.7%.
Requests decrease from 349 to 330, output tokens from 9,649 to 8,673, and summed trial time from 945.16 to 816.09 seconds.
These totals include changes in model-selected operations; they do not isolate the fixed display savings.
Sol makes more requests in the candidate, so the aggregate does not establish uniformly lower tool use across models.

Including all six source-selection trials, input tokens decrease from 310,019 to 256,773, requests from 369 to 351, output tokens from 10,330 to 9,357, and summed trial time from 989.44 to 896.08 seconds.
Those totals include the failed source-selection attempts and must not be interpreted as equal-quality savings.

To measure the fixed prompt change separately, the first user prompt of the earliest root Step is paired across all 94 main trials with identical case/model parameters and program text.
With `o200k_base`, total user-prompt text decreases from 14,362 to 10,626 tokens, or 26.0%.
Every paired prompt is smaller, by 25 to 41 tokens.
The integer assignment example decreases from 134 to 94 tokens.
These counts include the inspection preamble and context sections, and exclude system messages, tool/response schemas, and provider framing.
Aggregates, failures, and individual prompt pairs are saved in `comparison.json`.

## Artifacts and verification

Artifact names are relative to ignored `evals/outputs/` and have the prefix `20261029-context-guidance-`.

| Artifacts | Source fingerprint |
| --- | --- |
| `control`, `control-repeat`, `control-sources.tar.gz` | `cfa676f6f378dcc6729641c423187a96390e4aea3f825e50dbbc2acf80762c4e` |
| `candidate`, `candidate-repeat`, `candidate-sources.tar.gz` | `cbbad06b21425ec711b5e76a3dd2054ca43cb1397e0dc29623ff8a85a25f1db5` |

The control differs from its base revision only by the added integration case.
All shared integration cases are identical between the control and candidate snapshots.
The archive contents are checked against the run manifests, and final production source and tests are checked against the evaluated candidate.

Deterministic verification passes with 1,345 passed and 76 opt-in or environment-dependent skips in 48.57 seconds, recorded in `deterministic.xml`.
The existing OpenAI provider tests pass 12/12 in 71.22 seconds, recorded in `provider.xml`.
They cover native images, async execution, explicit host writeback, state carried between blocks, and decision probabilities; their existing settings remain unchanged, and their usage is excluded from the shared comparison.
Ruff lint and formatting, Pyright, strict documentation generation, and executable documentation examples pass.

Environment: Python 3.13.12, Nighthawk 0.17.0, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, OpenAI Python 3.35.1, MCP 2.3.0, and Claude Agent SDK 0.2.151.

## Limits

The comparison does not directly measure cognitive load or isolate the effects of async ordering from preamble removal.
The new omitted-contract case is developmental coverage, not an independent holdout.
Small repeated samples and synthetic tasks do not establish general reliability.
The explicit shadowed-source case remains a separate diagnostic; its prior sensitivity is not hidden by excluding it from the reported total.
Completed-agent usage is not a billing record and can omit failed or interrupted provider requests.
Concurrent batches mean elapsed times are observations rather than an isolated latency benchmark.
Coding-agent live tests remain disabled; their deterministic adapters are included in the full suite.
