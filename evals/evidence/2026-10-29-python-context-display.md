# Python-shaped Step context

## Decision and scope

This follow-up implements the approved P-SOURCE-001 and P-TYPE-001 decisions against revision `82b866a`.
The [earlier evaluation](2026-10-29-step-runtime-prompts.md) records the preceding design and its measurements; its results are not rewritten here.

Ordinary name resolution is owned by Python and the runtime.
Multiple stored sources do not require the model to choose a source for an ordinary variable reference.
The default prompt therefore removes both the conditional source-selection instruction and the collision scan that supported it.
The explicit `StepEnvironment.resolve(..., source=...)` API remains available.

Locals and globals use ordinary names and one type annotation.
The selected output contract takes precedence over a source annotation, with the current value's type as the fallback.
The `[output: ...]` and `[annotation: ...]` prefixes are removed, and writeback names receive no `<:name>` decoration in these sections.
Natural program declarations, tool admission, output validation, and finalization remain unchanged.

Complete scalar, list, and string-keyed dictionary previews use Python literals, including `None`, `True`, and `False`.
The existing bounded JSON renderer still supplies previews; literal translation preserves text inside strings.
Lossy previews use comments instead of assignments to invented values.
Unbound names have no initializer; object instances and native media are described without substituting a class or placeholder for their actual value.
Callable signatures, documentation, and async information remain visible in comments next to the binding annotation.
This is a bounded description, not executable initialization code.

Regression coverage checks contract priority over the current runtime type, empty generic containers, unbound outputs, Python literal preservation, shadowing, and unchanged media identity.
It also verifies that rendering does not execute annotation expressions, application equality, or application representation methods.
Callable annotation formatting now handles both `typing.Callable` and `collections.abc.Callable` parameter lists.
An unbound explicitly referenced name is described without resolving a shadowed value, including when a dotted media reference cannot be resolved.

## Evaluation protocol

The unchanged control is extracted from `82b866a` under ignored `evals/outputs/20261029-python-context-control-source`.
Both implementations run the same 46 shared cases with unchanged programs and assertions, once per model, followed by two additional repetitions of 11 selected cases.
Each implementation therefore has 136 trials, with fresh inputs for each trial.
No integration test is removed or relaxed, including the explicit source-selection diagnostic.

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
-k 'output_contract or application_inspect or explicit_task_source or helper_selection or python_call_preserves or truncated_input or return_preserves or (test_committed_values and (null or boolean))'
```

The control runs with its snapshot directory as the working directory and its own `src` and test root first on `sys.path`, using the same installed dependencies.
The repeated batches have a fixed size and retain failures; no case is retried until successful.
Separate provider tests exercise native media, async calls, embedded execution, decision probabilities, and state carried between blocks.
Their models and settings remain those of the existing tests, and their usage is excluded from the shared comparison.

## Results

| Cases | Control | Python-shaped context |
| --- | ---: | ---: |
| All shared trials | 136/136 | 134/136 |
| Ordinary execution, excluding explicit source selection | 130/130 | 130/130 |
| Explicit selection of a shadowed task binding | 6/6 | 4/6 |

The candidate's two failures are both Luna trials of `test_explicit_task_source_survives_python_shadowing`.
They return the effective Python value `7` instead of the explicitly requested task value `100`.
Luna passes 1/3 of these trials; Sol passes 3/3.
The direct source-selection API and its deterministic tests remain intact.
The source-selection instruction is not restored: the approved design delegates ordinary name resolution to the runtime, and this special discovery task does not justify including that instruction in every affected Step.

Completed-agent usage for the 130 ordinary trials:

| Variant | Model | Passed | Requests | Input tokens | Output tokens | Trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | Luna | 65/65 | 197 | 169,817 | 5,316 | 314.54 |
| Candidate | Luna | 65/65 | 190 | 157,562 | 5,465 | 292.76 |
| Control | Sol | 65/65 | 182 | 135,881 | 4,504 | 649.40 |
| Candidate | Sol | 65/65 | 181 | 132,187 | 4,521 | 663.96 |

Ordinary-trial input tokens decrease from 305,698 to 289,749, or 5.2%.
Requests decrease from 379 to 371, while output tokens increase from 9,820 to 9,986.
Including all six source-selection trials, input tokens decrease from 314,761 to 309,677, or 1.6%, and requests decrease from 392 to 390.
All-trial output tokens increase from 10,210 to 10,594, and summed trial time changes from 995.40 to 1,000.62 seconds.
These are observed usage differences, not a uniform cost or latency improvement.

For a comparison independent of subsequent tool-call choices, the first user prompt of the earliest root Step is paired across all 92 main trials.
Each pair has identical program text and case/model parameters.
Using `o200k_base`, their total text size decreases from 15,324 to 14,022 tokens, or 8.5%; every paired prompt is smaller, by 5 to 45 tokens.
The integer assignment example decreases from 148 to 134 user-prompt tokens.
These counts include the preamble and context sections, and exclude system messages, tool schemas, response schemas, and provider framing.
The aggregate and per-prompt pairs are saved in `comparison.json`.

## Artifacts and verification

Artifact names below are relative to ignored `evals/outputs/` and have the prefix `20261029-python-context-`.

| Artifact | Source fingerprint |
| --- | --- |
| `control-2`, `control-repeat` | `50debca9467f3cd43c4682eb4fdfcd4484db6b154f583501c162852488662eee` |
| `candidate` | `4831b4141da87bab5e391af6f44747aa8b95624a4252d71795e0491e0bd1f49e` |
| `candidate-repeat` | `7e4a010151ff24e3db33c673f9be7504c203b1fcb0ca958d727093a1170697a7` |

The two candidate fingerprints differ only in two deterministic tests updated to expect the new display text.
Production source and all shared integration tests are identical between those batches.
Candidate archives are `candidate-sources.tar.gz` and `candidate-repeat-sources.tar.gz`.
The control can also be reconstructed from the base Git revision.

An initial restricted-environment invocation used `control` and was interrupted without a terminal trial record.
It retains one `running` trial with no completed-step transcript or observed usage and is excluded from the completed comparison.
An attempted reuse of that output directory was rejected before execution; the successful control uses `control-2`.
These records are retained and do not establish that the interrupted invocation incurred no provider work.

Final deterministic verification: 1,344 passed and 75 opt-in or environment-dependent skips in 52.05 seconds, recorded in `deterministic.xml`.
The existing OpenAI provider tests pass 12/12 in 81.25 seconds, recorded in `provider.xml`.
Ruff lint and formatting, Pyright, strict documentation generation, and executable documentation examples pass.

Environment: Python 3.13.12, Nighthawk 0.17.0, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, OpenAI Python 3.35.1, MCP 2.3.0, and Claude Agent SDK 0.2.151.

## Interpretation and limits

The explicit source-selection case requests a task binding hidden by a Python binding of the same name.
It is retained as a diagnostic of special API discovery, not as justification for adding source-selection instructions to every ordinary Step.
Its failures must remain visible alongside ordinary execution results.
Applications needing both values can expose distinct ordinary names or explicitly select the source in their trusted program.

Python-shaped display is a design simplification; these measurements do not directly measure model cognitive load or establish general reliability.
The trials use synthetic tasks and existing assertions rather than an independent application holdout.
Small repeated samples do not establish equal failure probabilities.
Completed-agent usage can omit failed or interrupted provider requests and is not a billing record.
Some batches run concurrently, so their elapsed times are observations rather than an isolated latency benchmark.
Coding-agent live tests remain disabled; their deterministic adapter tests are covered by the full suite.
