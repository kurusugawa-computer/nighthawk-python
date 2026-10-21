# Pytest model evaluations

## Decision

Use pytest for model behavior and Natural composition evaluations.
Python fixtures create real values and callables, and ordinary assertions inspect committed outputs, identity, effects, exceptions, and control outcomes before any evidence serialization.
Remove the promptfoo provider, JSON/YAML fixture language, assertion adapters, CLI configuration, and tests that existed only to support those boundaries.
Historical evidence remains a description of its original runs; no result-format compatibility is maintained.

The current suite has 35 cases: 32 embedded model-behavior cases and three actual Natural-composition scenarios.
It does not reproduce the previous 53-case inventory one for one.
Repeated arithmetic and callable variants are consolidated around distinct contracts.
The helper cases now check returned object identity and exactly one requested invocation for synchronous and asynchronous functions.
Null and other output checks operate directly on Python mappings and values.
Composition includes Python iteration around judgments, model-selected child calls with explicit review policy, and ten ordered child calls sharing their counter through arguments.

The test runner adds only explicit model/settings selection, repetition, and evidence recording.
Pytest owns collection, selection, execution, assertions, phase failures, and exit status.
Each trial has fresh state; repetitions continue after failure unless the caller requests an early stop.
Records retain assertion failures, execution/setup/teardown errors, interruption, skip, and unattempted work separately.
Source hashes and available production model transcripts connect evidence to execution.

## Coverage and limits

| Concern | Current assessment |
| --- | --- |
| Values and output selection | Integer, float, boolean, text, list, null, mapping access/defaults, irrelevant context, and multiple committed outputs. |
| Effects | In-place list changes with dependent writeback, mapping replacement, and mutation before loop continuation. |
| Python calls | Actual calls, keyword-only arguments, awaited asynchronous execution, returned identity, and duplicate-effect detection. |
| Control | Resolved null/zero/integer returns, assignment before return, selected exception classes, deny frontmatter, and loop outcomes including Japanese instructions. |
| Instruction-like data | Exact results for text and mapping keys containing apparent instructions; not an isolation or security guarantee. |
| Natural composition | Existing application-like scenarios executed through the same configurable pytest fixture and recorded alongside embedded cases. |

Production execution and prompts are unchanged.
The new cases, assertions, settings, and sequential execution differ from the earlier promptfoo baseline, so their scores and wall times are not a controlled comparison of model quality or runner efficiency.
Small synthetic cases and repeated formulations do not establish general application reliability or independent population estimates.
Completed-agent usage omits some failed or interrupted provider requests and does not represent billing.
Opaque values are recorded as type markers; assertions check identity before serialization.
Abrupt process termination can leave a running record without a final summary.

## Measured runs

All runs used `openai-responses:gpt-5.6-luna`, Python 3.13.12, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, and OpenAI Python 3.35.1.
The editable Nighthawk version was 0.17.0 at base revision `167936d`, with the evaluation changes in the working tree.
No production source was changed.

| Artifact directory under `evals/outputs/` | Cases | Repeats | Passed | Assertion failures | Other errors | Observed requests | Input tokens | Output tokens | Sum of trial seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `20261021-pytest-smoke` | 2 | 1 | 2 | 0 | 0 | 4 | 3,144 | 177 | 27.924 |
| `20261021-pytest-production` | 35 | 3 | 97 | 2 | 6 | 388 | 379,824 | 15,644 | 922.946 |
| `20261021-pytest-assertions` | 4 | 3 | 6 | 4 | 2 | 20 | 15,198 | 969 | 43.173 |

The smoke used default model settings.
The full matrix and focused follow-up supplied `{"openai_reasoning_effort":"low"}`.
Both completed their scheduled trials and exited with pytest status 1 because assertions or execution failed.
No trials were skipped, interrupted, or discarded.
No live coding-agent evaluation was performed.

The full matrix command was:

```bash
uv run --env-file .env pytest -q tests/integration/ -m model_evaluation \
  --evaluation-model openai-responses:gpt-5.6-luna \
  --evaluation-repeat 3 \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/20261021-pytest-production
```

The initial source fingerprint was `d7539ff9b2df4c5de34bb7e93402252ae8b8d55cb8b5a0c7b32f9fe7f558304d`.
The focused follow-up used `eba8fbf2fd9b3c2f56b87ac65b096bca0173ea2f26237964a485058c2a6c6f7d` and selected `python_call_preserves_identity or selected_exception` from `test_model_behavior.py`.
Each manifest records the individual source-file hashes behind its fingerprint.
The full-matrix directory also retains `sources.tar.gz`, whose contents match its manifest.
After these runs, default pytest discovery was restricted to `tests/`; the measured commands already selected explicit paths under that directory.

## Failures and interpretation

All nine actual Natural-composition trials passed, including all three ten-child executions.
The full matrix's eight unsuccessful trials were confined to two contracts:

- Two of six identity/effect trials invoked the helper through evaluation, discarded its opaque result, and completed without committing `receipt`.
  One subsequently attempted to recover the value from an absent `_` binding.
  Calls and effects remained visible, but the required returned object was missing.
- All six exception-selection trials failed: four selected the default `Exception` instead of the supplied error class; two raised the requested Python exception during execution instead of completing a DSL `Raise`.

The initial missing-output checks surfaced as `KeyError`; the final test first asserts the committed key set.
Exception tests now explicitly check the selected class and message after a DSL Raise, while propagating ordinary execution failures.
The recorder also recognizes `pytest.fail()` as an assertion failure.
These changes improve diagnostic classification without changing prompts, accepting missing outputs, or relaxing the exception contract.

The focused follow-up passed all six helper trials, but the earlier missing-output failures remain evidence of variation.
Its six exception trials remained unsuccessful: four wrong selected classes and two ordinary execution errors.
The failing model behavior is retained as failing opt-in evaluation coverage; it is not marked expected, retried into success, or repaired by substituting evaluation-only runtime behavior.
The observed task failures do not establish a defect in the pytest runner, and successful runner checks do not establish reliable model behavior.

## Deterministic verification

The final default suite passes: 1,292 passed and 64 opt-in/version-dependent skips on Python 3.13.
All ten recorder tests also pass on Python 3.14 and 3.15.
Those tests use real pytest subprocesses to check model/repetition selection, fresh state, retained effects and usage, assertion and explicit pytest failures, setup/teardown errors, skipped work, early stops, cancellation, keyboard interruption, output-directory protection, and diagnostic serialization.
Ruff lint/format checks, Pyright, and the strict MkDocs build pass.
