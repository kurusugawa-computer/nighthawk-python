# Exception binding selection and structured outcomes

## Decision

Expose exception classes by their Python binding names, including aliases such as `selected_error = ValueError`.
The previous executor required a binding's name to equal the class's `__name__`, excluding an exception passed through an ordinary function argument or embedded input.
This made the requested DSL Raise impossible to express in structured output.
Keep exception resolution and commit inspection in the existing runtime pipeline.

Generate outcome variants from the Pydantic models instead of maintaining duplicate handwritten schemas.
Constrain `raise_error_type` to the visible exception binding names or `null`.
Null and omission retain the default `Exception`; a named selection resolves the corresponding class.
This also preserves the default when providers require every field in strict structured output, as described in the [OpenAI structured output guide](https://developers.openai.com/api/docs/guides/structured-outputs#all-fields-must-be-required).

## Controlled exception comparison

The unchanged `test_selected_exception` case selects `ValueError` or `TypeError`, then checks the actual exception class, message, and DSL Raise lifecycle record.
Each of two models ran both variants three times, with fresh inputs and `{"openai_reasoning_effort":"low"}`.
No case, assertion, tool, or model setting changed between control and treatment.
Production changes were the alias filter correction, Pydantic-derived schemas, nullable selection, and explicit exception-selection guidance.

| Runtime | Model | Passed | Assertion failures | Execution errors | Observed requests | Input tokens | Output tokens | Sum of trial seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Control | `openai-responses:gpt-6-luna` | 0 | 6 | 0 | 6 | 4,146 | 156 | 9.923 |
| Control | `openai-responses:gpt-6.1-sol` | 0 | 2 | 4 | 2 | 1,382 | 60 | 13.594 |
| Treatment | `openai-responses:gpt-6-luna` | 6 | 0 | 0 | 6 | 4,548 | 198 | 12.105 |
| Treatment | `openai-responses:gpt-6.1-sol` | 6 | 0 | 0 | 6 | 4,548 | 198 | 20.873 |

The control produced the generic `Exception` in eight trials and raised the requested exception during expression evaluation in four trials, bypassing the required DSL Raise.
The treatment passed all 12 trials.
Pytest elapsed times were 23.60 seconds for the control and 33.05 seconds for the treatment.
The failed control attempts remain in the artifacts; neither run used success-seeking retries.

Run each phase with its corresponding source snapshot and a new artifact directory:

```bash
uv run --no-sync --env-file .env pytest -q --tb=short \
  tests/integration/test_model_behavior.py -k selected_exception \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-model openai-responses:gpt-6.1-sol \
  --evaluation-repeat 3 \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/20261026-exception-alias-treatment
```

## Provenance

Both runs used base revision `8f3e9fbd6983f99aea4f2155ae1ca94f98bf0f85`; the control tree was clean and the treatment contained the changes described above.
Artifacts include manifests, individual trials, summaries, and `sources.tar.gz` archives verified against every manifest file hash.
The archives exclude credentials.

| Artifact directory under `evals/outputs/` | Source SHA256 |
| --- | --- |
| `20261026-exception-alias-control` | `b0a353d5952a91f060cb2b3c161ee22f65a279aa1898dc9e20318b8a13689877` |
| `20261026-exception-alias-treatment` | `ce5cde50ca9ce5cb98b3a16ab8dbb9707b082898708ce63afdf06435e611c564` |
| `20261026-outcome-schema-shared` | `e96f5fd861efe6e5f09ecfaf3f364f2f1fa65a409eb848b419556dce806c1d72` |

The environment uses Python 3.13.12, Nighthawk 0.17.0, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, OpenAI Python 3.35.1, MCP 2.3.0, and Claude Agent SDK 0.2.151.

## Shared evaluation

The final schema implementation also consolidates exception-name lookup into one loop and includes the native-image transport regression.
Its complete shared evaluation ran 35 unchanged cases per model once with the same low reasoning setting.
Use the command above with `tests/integration/ -m model_evaluation`, remove `-k selected_exception`, set `--evaluation-repeat 1`, and select a new output directory.

All 70 trials completed: 68 passed and two failed in 595.62 seconds.
Luna passed 33 of 35 cases and Sol passed all 35.
All exception and Natural-composition cases passed.
Luna's synchronous and asynchronous helper cases called the helper once but did not commit the returned object to `receipt` (`trial-0031.json` and `trial-0032.json`).
The asynchronous case also attempted to recover the value from an absent `_` binding.
These failures remain visible and motivate a separate comparison of result-retention instructions.
One repetition per shared case is regression evidence, not a reliability estimate or a controlled comparison with earlier full-suite scores.

| Model | Observed requests | Input tokens | Output tokens | Sum of trial seconds |
| --- | --- | --- | --- | --- |
| `openai-responses:gpt-6-luna` | 127 | 130,756 | 4,301 | 245.760 |
| `openai-responses:gpt-6.1-sol` | 124 | 116,878 | 3,170 | 349.666 |

The separate result-retention control overlapped the end of this run, so these wall times are not isolated performance measurements.

## Deterministic checks and limits

Eight regression variants exercise Natural functions and embedded steps, both exception classes, named selection, and the null default.
They check the schema presented to the model and the final exception's identity in the terminal lifecycle record.
The regression failed before the production change because the selection field was absent.
The complete deterministic suite passes with 1,324 passed and 64 skipped; Ruff, Pyright, strict documentation generation, and rendered internal links also pass.

A separate mock OpenAI transport check verifies that three binary image inputs reach the Responses request as distinct native images with their corresponding binding labels.
This checks serialization, not visual interpretation.
The previously observed live image-input failure, where the model imported unavailable `PIL`, remains recorded in the [model migration evidence](2026-10-21-gpt6-model-migration.md#provider-and-codex-integration).
No image-processing dependency or relaxed image assertion was introduced.

The exception comparison establishes the observed behavior of these two cases and models; three repetitions do not establish general reliability.
Completed-agent usage can omit failed requests and is not a billing record.
The schema and instruction changes were evaluated together, so the comparison does not isolate their individual effects.
