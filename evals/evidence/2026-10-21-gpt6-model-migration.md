# GPT-6 model migration

## Decision

Use `gpt-6-luna` for routine OpenAI tests and documentation examples, and `gpt-6.1-sol` for higher-capability comparisons and the autonomous diagnosis example.
Codex integration tests select `codex:gpt-6-luna` explicitly, including the skill tests that otherwise inherit the local CLI model.
The specification and deterministic backend test retain coverage of `codex:default` as a supported configuration.
Historical evidence and released changelog entries retain their original model identifiers and measurements.

Production source, model defaults, prompts, and live integration/evaluation assertions and cases are unchanged.
Existing integration reasoning settings are preserved.
The provider settings example uses `openai_reasoning_effort: low`; the [OpenAI migration guidance](https://developers.openai.com/api/docs/guides/latest-model#migration-quickstart) excludes `temperature` when reasoning effort is not `none`.
Deterministic mock responses and configuration examples also select GPT-6 Luna.
The borrowed-client mock retains its temperature-propagation assertions with explicit `openai_reasoning_effort: none`.
The `gpt-4` tokenizer fixture remains solely to verify inference of `cl100k_base`; it performs no provider request.

## Method and provenance

The shared pytest evaluation runs 35 cases for each of two models, with three repetitions per case: 210 scheduled trials.
Both models use `{"openai_reasoning_effort":"low"}`.
Every repetition runs sequentially with fresh fixture state and the production runtime.
Failures are retained without success-seeking retries, assertion changes, or model substitutions.

```bash
uv run --env-file .env pytest -q --tb=short tests/integration/ -m model_evaluation \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-model openai-responses:gpt-6.1-sol \
  --evaluation-repeat 3 \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/20261021-gpt6-migration
```

The base revision is `f59943b4e1ffbf0957aa7ef5c74ed2ce8941c0f8`, with the model-selection changes in the working tree.
The source fingerprint is `bd31baef0f01a685f851d57f1c63fe92a65fb4751947f8cef46a2c6229f8d55d`.
The artifact directory contains `manifest.json`, individual trial records, `summary.json`, and `sources.tar.gz` containing the files named by the manifest.
Integration logs and JUnit reports are retained there alongside the evaluation log and deterministic verification logs.
After the evaluation source snapshot, two deterministic test files were updated to replace their unrelated mock model identifiers and configure the borrowed-client mock's reasoning effort.
The selected integration cases and production source remain identical to the recorded snapshot.

The environment uses Python 3.13.12, Nighthawk 0.17.0, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, OpenAI Python 3.35.1, MCP 2.3.0, and Codex CLI 0.163.0.
The OpenAI model metadata endpoint returned both requested model identifiers before the evaluation started.
Codex integration uses the existing ChatGPT login and does not supply a reasoning-effort override.

## Shared model evaluation

| Model | Trials | Passed | Assertion failures | Execution errors | Observed requests | Input tokens | Output tokens | Sum of trial seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `openai-responses:gpt-6-luna` | 105 | 96 | 9 | 0 | 395 | 395,690 | 11,786 | 694.843 |
| `openai-responses:gpt-6.1-sol` | 105 | 99 | 0 | 6 | 360 | 325,603 | 9,192 | 919.199 |

All 210 scheduled trials ran; none were skipped, interrupted, or left unattempted.
Pytest reported 195 passes and 15 failures in 1,614.45 seconds and exited with status 1.
The evidence recorder distinguishes the nine assertion failures from the six execution errors.
Natural composition passed seven of nine Luna trials and all nine Sol trials.

Observed failures are retained in the trial artifacts:

- GPT-6 Luna invoked the synchronous helper once but did not commit the required `receipt` in one of six identity/effect trials (`trial-0093.json`).
  The calls observation retains the requested invocation; the committed binding mapping is empty.
- All six GPT-6 Luna exception-selection trials returned the generic `Exception` class instead of the requested `ValueError` or `TypeError` (`trial-0127.json` through `trial-0132.json`).
- All six GPT-6.1 Sol exception-selection trials raised the requested Python exception inside `nh_evaluate` rather than completing the requested DSL Raise (`trial-0133.json` through `trial-0138.json`).
  The recorder classifies these as execution errors and retains the tracebacks.
- One of three GPT-6 Luna review-policy composition trials omitted the claim whose number was spelled out as a word (`trial-0200.json`).
  Its generated filter checked for digit characters, so it called only one child and produced two completed steps instead of the required three.
  GPT-6.1 Sol passed all three review-policy composition trials.
- One of three GPT-6 Luna counter-composition trials called the first child twice, first through `nh_evaluate` and then while assigning the initial messages list (`trial-0206.json`).
  It produced 12 completed steps instead of the required 11, even though the final list contained ten messages.
  GPT-6.1 Sol passed all three counter-composition trials.

## Provider and Codex integration

| Suite | Passed | Failed | Skipped | Seconds |
| --- | --- | --- | --- | --- |
| OpenAI execution, carry, and Decisions, with the separately gated image tests disabled | 10 | 0 | 2 | 68.07 |
| OpenAI image inputs and image tool results, with both integration gates enabled | 1 | 1 | 0 | 22.66 |
| Codex tools and skills | 3 | 1 | 0 | 148.97 |

The two initially skipped image cases were subsequently executed once with `NIGHTHAWK_OPENAI_MULTIMODAL_INTEGRATION_TESTS=1` in addition to `NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1`.
Across distinct OpenAI integration cases, 11 passed and one failed.
Codex integration uses `NIGHTHAWK_CODEX_INTEGRATION_TESTS=1`.

The image-input test failed when the model attempted to import `PIL`, which is not installed, producing `ModuleNotFoundError`.
The image-tool-result case passed.
The Codex custom-tool test raised `Exception: Name 'test_operation' is absent from the effective environment` instead of completing the requested native tool call.
The basic Nighthawk tool case and both skill cases passed.
These failures remain visible; no dependency was added and no prompt or assertion was weakened to turn them into successes.

## Deterministic verification

- Default pytest suite: 1,292 passed, 64 skipped, one upstream event-loop deprecation warning.
- Documentation tests after the provider settings example adjustment: 30 passed, one skipped.
- Ruff format and lint checks: passed.
- Pyright: zero errors and warnings.
- Strict MkDocs build: passed.

Initial sandboxed test attempts were interrupted after local socket binding and external DNS resolution were found to be prohibited.
The recorded completed test runs used an environment permitting the required MCP sockets and provider communication.
The sandbox interruptions are infrastructure attempts, not completed model-evaluation trials.

## Limits

Recorded token usage covers completed model agents and can omit failed or interrupted provider requests; it is not a billing record.
Trial durations and token counts describe these cases, settings, and environment, not general model performance.
The earlier stored evaluation has a different source fingerprint; these results establish a new baseline rather than a controlled reproduction of its scores.
Small synthetic cases and three repetitions do not establish application reliability or universal model superiority.
