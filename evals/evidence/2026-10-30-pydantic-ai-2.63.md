# Pydantic AI 2.63 upgrade

## Decision and scope

This change upgrades `pydantic-ai-slim` from 2.59.0 to 2.63.0 against revision `5cc571e` and keeps one minor series, `>=2.63.0,<2.64`.
Ordered dispatch still overrides internal Pydantic AI methods; the [operation inspection](../../docs/specification.md#103-operation-inspection) contracts are retained without relaxation.

Pydantic AI 2.63 deprecates `RetryPromptPart`.
A retry that answers a tool call is a `ToolReturnPart` with `outcome="retried"`, and output feedback without a call is a `RetryFeedbackPart`, which models receive as a fenced user prompt.
Nighthawk adapts as follows:

- Native-output refusal feedback under `early` is a `RetryFeedbackPart` that records its output-retry attempt.
- A skipped output candidate under `exhaustive` records the same failed `skipped` tool return as under `graceful` and `early`, instead of a retry prompt.
- Wrapped dispatch generators are closed with their callers, matching Pydantic AI's `aclosing` delegation.
- Coding-agent tool transports report argument validation failures as the validation-error JSON array of a retried tool return, without the former `Fix the errors and try again.` suffix.
  MCP and SDK transports still mark these results as errors.

Nighthawk's refusal and skip payloads are unchanged.
The model-facing differences are Pydantic AI's retry representation and the coding-agent validation text.

## Comparison protocol

The control is an immutable source snapshot of `5cc571e` with its locked Pydantic AI 2.59.0 environment, source fingerprint `62b8005186e34835d4683b7b7979406b7d5433f6073e2ed62703562c97e0713d`.
The candidate is the upgraded working tree, source fingerprint `ec72a3dc2b0fbc7c6a90232d5e278c0f55c5f1e5d36fd9dd3095ba4179d64309`.
Both use production defaults without evaluation settings and fresh fixture state for each trial.

1. The shared model evaluation suite runs all 50 cases three times on the candidate with `openai-responses:gpt-6-luna`.
   Its only failing case is rerun three times on the control.
2. Four refusal and retry probes run five times on each implementation with `openai-responses:gpt-6-luna`.
3. One coding-agent validation probe runs three times on each implementation with `claude-code-cli:haiku` and `codex:gpt-6-luna`.
4. Existing live provider and coding-agent integration tests run with their own settings.

The probes are ad hoc trusted programs authorized by the repository owner for this upgrade; they are not repository evaluation cases.
They assert committed values, actual tool calls, and operation statuses:

| Probe | Task and assertions |
| --- | --- |
| Tool refusal | Send a report to `public`; the host refuses with guidance to use `internal`. Only `internal` receives the report and is committed. |
| Return refusal | Return `legacy_total()`; the host refuses that expression with guidance to return `audited_total()`. The refused helper never runs and 42 is returned. |
| Batch skip | Record 1, 2, and 3 in one parallel batch; the host refuses 2. Exactly 1 and 3 are recorded. |
| Argument retry | Set level 10; an argument validator raises `ModelRetry` outside 1..3. Only level 3 is applied and committed. |
| Coding-agent validation | Set level 10 through the backend's tool transport; an `AfterValidator` absent from the JSON schema rejects values outside 1..3. Level 10 records a failed admission, only level 3 is applied, and 3 is committed. |

The evaluation command is:

```bash
uv run --env-file .env pytest -q tests/integration/ -m model_evaluation \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-repeat 3 \
  --evaluation-output evals/outputs/<new-directory>
```

## Results

### Shared evaluation suite

The candidate passes 147/150 trials.
All three failures are trials of `test_explicit_task_source_survives_python_shadowing`, which return the Python value 7 instead of the requested task value 100.
The control fails the same case 0/3 with the same assertion.
This matches the Luna sensitivity recorded in the [context guidance](2026-10-29-context-guidance.md) and [Python context display](2026-10-29-python-context-display.md) evaluations.
The other 49 cases pass 147/147.

Completed-agent usage is 488 requests, 416,314 input tokens, and 20,535 output tokens, with 1,141.14 summed trial seconds.

### Refusal and retry probes

| Probe | Control | Candidate |
| --- | ---: | ---: |
| Tool refusal | 5/5 | 5/5 |
| Return refusal | 5/5 | 5/5 |
| Batch skip | 5/5 | 5/5 |
| Argument retry | 5/5 | 5/5 |

Every batch-skip trial issued a parallel batch, recorded `succeeded`, `rejected`, and `skipped`, then reissued the skipped call.
In the candidate transcripts, the return refusal and the argument retry arrive as `ToolReturnPart` with `outcome="retried"`; the control used `RetryPromptPart`.
Denied refusal and failed skip returns are identical across implementations.
Every return refusal used the output-tool route; the model never co-emitted native output with tool calls under `early`.
Summed trial time is 145.6 seconds for the control and 137.8 seconds for the candidate.

### Coding-agent validation probe

| Backend | Control corrected | Candidate corrected | Control all assertions | Candidate all assertions |
| --- | ---: | ---: | ---: | ---: |
| Claude Code CLI, `haiku` | 3/3 | 3/3 | 3/3 | 2/3 |
| Codex, `gpt-6-luna` | 3/3 | 3/3 | 2/3 | 3/3 |

Every trial records the failed admission of level 10 and then applies only level 3.
The two incomplete trials leave the output binding at 0 after applying level 3.
In a screening trial with `Field(ge=1, le=3)`, Claude Code sent 3 directly, so the final probe moves the constraint outside the JSON schema.

### Live integration tests

The OpenAI provider tests, including native images, pass 12/12 on the candidate.
The Claude Code CLI tests pass 4/4 on the candidate with the CLI's existing login.
With the local environment file loaded, its Anthropic credential was rejected with HTTP 401 before any model work, so those failures do not assess Nighthawk.
The Claude Agent SDK tests are skipped because their gate requires an Anthropic credential variable.
Codex passes 2/4 on both implementations.
In both, `test_codex_natural_step_uses_tool` commits an empty result and `test_codex_natural_step_uses_custom_nh_tool` fails on a model-selected expression.

## Artifacts and verification

Artifact names are relative to ignored `evals/outputs/`:

- `20261030-pydantic-ai-2.63-evaluation`: candidate manifest, trial records, and summary.
- `20261030-pydantic-ai-2.63-probes`: probe programs, per-trial records with transcripts, and coding-agent probe logs.

The control's rerun of the failing evaluation case and the integration-test logs were not retained.

Deterministic verification passes with 1,349 passed and 79 opt-in skips.
Ruff lint and formatting and Pyright pass.

Environment: Python 3.13.12, Nighthawk 0.18.0, pytest 9.2.0, pytest-asyncio 1.4.1, Pydantic 2.14.2, pydantic-ai-slim 2.63.0 and 2.59.0 for the control, OpenAI Python 3.41.0, MCP 2.3.0, Claude Agent SDK 0.2.175, Claude Code 2.1.314, and Codex CLI 0.166.0.

## Limits

Only the failing case of the shared suite is compared with the control; the other cases ran on the candidate alone.
Three to five trials per synthetic probe do not establish general reliability.
Probe usage was not recorded, and completed-agent usage is not a billing record.
The native-output refusal path under `early` and the Claude Agent SDK backend have deterministic coverage only.
Evaluation and probe batches ran concurrently, so elapsed times are observations rather than a latency benchmark.
