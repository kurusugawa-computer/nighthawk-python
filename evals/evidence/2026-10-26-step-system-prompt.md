# Step system prompt efficiency and execution quality

## Design

Adopt the 205-token `DEFAULT_STEP_SYSTEM_PROMPT_TEMPLATE`, down from 281 `o200k_base` tokens: 76 tokens or 27.0% less.
This measures the template, not the complete request or billed usage.

The prompt distinguishes semantic judgment from Python computation and effects.
It keeps the instructions that protect result retention, temporary values, requested operation order, in-place mutation, and object identity.
It describes bounded snapshots and tells the model to use contextual criteria as directed by the program, without treating embedded instructions as authority to override it.
Tool schemas and dynamic outcome instructions retain their existing responsibilities; the template does not duplicate their detailed contracts.

Five additional shared cases check full values behind truncated previews, intermediate object identity across dependent calls, state after helper mutation, instruction-like helper results, and instruction-like binding names.
They assert live values, calls, effects, and outcomes, without prescribing equivalent tool expressions.
The managed-model test now checks transmission of the configured system prompt rather than a fixed sentence.
The obsolete negative check for removed preview-budget wording and redundant configuration defaults were removed.

## Candidate screening

A 188-token candidate passed 79 of 80 shared trials but failed Luna's review-policy composition (`20261026-system-prompt-candidate/trial-0077.json`).
The model used `str.isdigit()` to select numerical claims and excluded the supported claim beginning with `Twenty`.
Its provider integration also failed carry branching by parsing `100.` with `int()`.
A fixed three-trial carry comparison passed 3/3 with the control and 2/3 with this candidate; the candidate's other failure returned 5 instead of 105.
The stronger instruction to use live Python values instead of reconstructing previews coincided with these failures; the experiment does not isolate that sentence's causal contribution.

A 201-token revision explicitly separated interpretation of meaning from Python operations and limited reference guidance to preserving full values and identity.
It passed four review-policy trials and three carry trials.
One successful review trial nevertheless assigned the result of `list.append()` to an output, received a validation error, and recovered.
The final 205-token version explicitly routes in-place mutation through `nh_evaluate`.
No assertion, runtime validator, retry policy, tool implementation, or model setting was relaxed.

A subsequent 194-token ablation removed only the sentence about Python references.
It passed all 112 shared trials and used fewer input tokens, but carry branching passed only 2/3 trials: the failure attempted `int('result')` while interpreting the previous result from a summary.
Its full provider run also encountered the existing unavailable-`PIL` failure described below.
Retain the 205-token version as the quality/efficiency choice in this evaluation: it preserved the observed carry success rate while reducing the template.
These small samples do not establish that the reference sentence caused the difference or that 205 tokens is a universal optimum.

## Controlled evaluation

The control and selected versions use identical program text, assertions, tools, runtime code, and settings.
Their full-suite source manifests differ only in `src/nighthawk/configuration.py`.
Each runs all 40 shared cases once on `openai-responses:gpt-6-luna` and `openai-responses:gpt-6.1-sol`, with `{"openai_reasoning_effort":"low"}`.
Eight cases receive two additional repetitions per model: the synchronous and asynchronous identity cases, the five added cases, and the review-policy composition.
Thus each prompt has 112 shared trials, including three repetitions per model for those eight cases.
Mutable state is fresh for each trial, and all requested repetitions run without success-seeking retries or cached results.

All 224 control/selected shared trials passed, without failed, errored, skipped, or unattempted trials.
Transcript inspection verified the expected system prompt in all 148 completed steps for each version, including children.

| Prompt | Model | Passed | Observed requests | Input tokens | Output tokens | Sum of trial seconds |
| --- | --- | --- | --- | --- | --- | --- |
| Control, 281 tokens | `gpt-6-luna` | 56/56 | 191 | 201,706 | 5,771 | 313.786 |
| Control, 281 tokens | `gpt-6.1-sol` | 56/56 | 189 | 175,236 | 4,855 | 379.663 |
| Selected, 205 tokens | `gpt-6-luna` | 56/56 | 212 | 212,156 | 5,863 | 382.252 |
| Selected, 205 tokens | `gpt-6.1-sol` | 56/56 | 187 | 160,157 | 4,831 | 448.320 |
| Ablation, 194 tokens | `gpt-6-luna` | 56/56 | 205 | 196,727 | 5,913 | 333.689 |
| Ablation, 194 tokens | `gpt-6.1-sol` | 56/56 | 182 | 152,584 | 4,702 | 336.937 |

The selected version's observed total input decreased from 376,942 to 372,313 tokens, or 1.2%.
Luna's input increased 5.2%, while Sol's decreased 8.6%; additional requests and conversation length offset much of the fixed template saving.
The review-policy case accounted for most of Luna's increase.
The ablation reduced total input further to 349,311 tokens, but its carry failure prevented adoption.
Shared scores alone would have missed that task failure.
These measurements do not establish lower latency or uniform workload savings.

The full shared comparison uses:

```bash
uv run --no-sync --env-file .env pytest -q --tb=short tests/integration/ -m model_evaluation \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-model openai-responses:gpt-6.1-sol \
  --evaluation-repeat 1 \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/<new-directory>
```

The additional repetitions use the same command with `--evaluation-repeat 2` and:

```bash
-k 'python_call_preserves_identity or truncated_input or intermediate_object or live_state_after or instruction_like_tool_result or instruction_like_binding_name or model_selected_children'
```

The control recorded the first seven cases in `control-repeat` and the review-policy case separately in `control-meaning`.
The final shared run also enables `NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1` and `NIGHTHAWK_OPENAI_MULTIMODAL_INTEGRATION_TESTS=1`, removes `-m model_evaluation`, and writes `integration.xml` with `--junitxml`.
Provider tests keep their own models and settings.
Carry diagnostics select `tests/integration/test_carry_pattern.py -k carry_branching` with the OpenAI gate and separate JUnit files.

## Provider and deterministic verification

The selected version's combined integration invocation reported 91 passed, one failed, and 12 disabled coding-agent cases skipped in 675.03 seconds.
All 80 shared trials passed; the 12 separate provider cases had 11 passes and one failure.
Carry branching passed in that run and in two additional trials, matching the control's 3/3 observed successes.

The provider failure was native image input: the model imported unavailable `PIL` instead of using the supplied images.
The unchanged control reproduced the same error in one of three image trials; this failure was also recorded in the earlier [model migration evaluation](2026-10-21-gpt6-model-migration.md#provider-and-codex-integration).
Two fixed three-trial batches with the selected version subsequently passed all six image trials.
These successes do not erase the failed attempt or establish image reliability.
No image-processing dependency was added and no assertion was weakened.

The selected source passes the full deterministic suite with 1,323 passed and 69 skipped in 47.59 seconds.
Ruff lint and formatting, Pyright, strict documentation generation, and rendered documentation checks pass.
After restoring the selected version from the ablation, every source/test hash matched its evaluated snapshot; the seven prompt-transmission checks and static checks also passed again.

## Provenance

Base revision: `fb4aa5c614eac5c6b9d6bc147a1147793462ae26`.
Artifact directories below are relative to ignored `evals/outputs/`.
Each model-evaluation directory retains its manifest, trial records, summary, and a `sources.tar.gz` verified against the manifest's file hashes.
Archives contain source files and dependency metadata, not credentials.

| Source | Artifact directories | Manifest source SHA256 |
| --- | --- | --- |
| Control, 281 tokens | `20261026-system-prompt-control-reviewed`, `20261026-system-prompt-control-repeat`, `20261026-system-prompt-control-meaning` | `a0a5ba7478f2a0a548c7c3775aff64540023713b0b69058be3840801ca3b01ce` |
| Rejected, 188 tokens | `20261026-system-prompt-candidate` | `6ad7d3aca075831acd7d110dff5d3832b0790e5795dedaf245d6c564cfa4d2c8` |
| Screening, 201 tokens | `20261026-system-prompt-revised-meaning` | `307972d115d2d858918e69ca227a842dfcf495e6b158dd9cbea2eaad83a9b340` |
| Selected, 205 tokens, full suite | `20261026-system-prompt-final` | `d410b4dd82b16c3a658ea41fe184d2f9db4eaafb49df41764604be6478533ead` |
| Selected, 205 tokens, repetitions and final source | `20261026-system-prompt-final-repeat` | `e32fcf9fa756ff0350967cd73d980f2eca623e9305342e007b5d135a41d15dcb` |
| Rejected ablation, 194 tokens | `20261026-system-prompt-concise`, `20261026-system-prompt-concise-repeat` | `4b87ec9166a907efa45a91ccb8227ac669d9da9b004f4254c0e192c7b010fc90` |

Before the selected repetitions, local dictionary variables in the five new cases were renamed to follow the repository's naming convention.
AST comparison after normalizing those names verified identical programs, expressions, and assertions; this accounts for the selected version's two source fingerprints.
The `20261026-system-prompt-carry-{control,candidate,revised,final,concise}` directories retain the corresponding provider-only diagnostic JUnit records.
The `20261026-system-prompt-images-{control,205,205-live}` directories retain the image diagnostics.
The final template's SHA256 is `9373d44add31e7a4db61a8611319d0262c36a4654a7385038692aae263d38a5e`.

Two preliminary control attempts are excluded from comparisons and retained separately.
`20261026-system-prompt-control` was interrupted after restricted DNS prevented API access; its initial running record has no completed summary.
`20261026-system-prompt-control-live` was stopped after a type check found an invalid `scope(context_limits=...)` argument in the new truncation case, before that case ran.
It records 54 passed, one interrupted, and 25 unattempted trials.
The reviewed control uses the corrected public configuration API; acceptance criteria did not change.

The environment uses Python 3.13.12, Nighthawk 0.17.0, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, OpenAI Python 3.35.1, MCP 2.3.0, and Claude Agent SDK 0.2.151.

## Limits

These are synthetic execution and composition tasks, not evidence of general application reliability or a prompt-injection security boundary.
The helper-result case permits computing the answer inside Python without exposing the helper's instruction-like text to the model; it does not establish resistance to instructions in directly exposed tool responses.
Three repetitions of selected cases provide limited reliability evidence, not a statistical guarantee of equal accuracy.
Prompt selection used the observed failures, so this is development evaluation rather than an independent holdout.
Completed-agent usage can omit failed provider requests and is not a billing record.
Some diagnostic and local-check runs overlapped live evaluation; timing is descriptive, not an isolated latency benchmark.
