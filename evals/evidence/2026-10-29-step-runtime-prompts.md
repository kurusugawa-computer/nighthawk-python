# Step runtime prompt efficiency and execution quality

## Design

The change covers the base system template, dynamic context in `runtime/prompt.py`, control-result instructions and schemas in `runtime/model_outcome.py`, and their executor integration.
The base template adds 13 tokens to distinguish semantic judgment from keyword matching and guessed text formats.
The Python operation tools remain unchanged.
The [philosophy](../../docs/philosophy.md) favors understandable contracts and total task quality over the shortest possible prompt.

- Ordinary bindings show their reference, value, and available annotation or output contract without repeating their name and origin.
  Python, task, and step provenance and shadowed alternatives remain available through environment discovery and named descriptions.
  The per-name collision listing is removed; the model can use the displayed effective values directly.
  When relevant names have multiple sources, a single conditional line gives `environment.resolve('name', source='task')` and the other supported sources for explicit source-selection requests.
- Output contracts use a compact prefix; an identical declaration annotation is not repeated.
  A dotted media leaf does not inherit the root object's annotation or output contract.
- Callables retain their reference, signature, meaningful documentation, and asynchronous marker.
  The separate signature-collision scan and repeated `disambiguation: use name` hints are removed.
- The response schema owns allowed kinds, required fields, return-expression semantics, and exception binding selection.
  Generated titles and the parallel prose enumeration of the schema are removed.
  Completion guidance keeps an explicit `pass` example and distinguishes output assignment from returning from the Python function.
- Runtime inspection explicitly returns a metadata dictionary containing type, members, and preview.
  The preamble offers this access for details missing from the snapshots.
  Native media gets a short direct-perception instruction only when it is actually included in the prompt.

No output validator, operation policy, retry budget, assertion, or model setting is relaxed.
The specification and five executable documentation prompt examples are updated together.

## Evaluation protocol

Base revision: `1a1e28b`.
The unchanged production control is executed from a source snapshot with the same additional evaluation cases as the final treatment.
The added cases check opaque return identity, the default exception class, selection between equally shaped helpers by documented purpose, a literal output contract, application names shadowing task references, and explicitly selecting a shadowed task source.
Assertions inspect actual Python values, identity, calls, and control results.

Both models use `{"openai_reasoning_effort":"low"}`:

- `openai-responses:gpt-6-luna`
- `openai-responses:gpt-6.1-sol`

The final main comparison runs all 46 shared cases once per model.
Seventeen selected cases receive two additional repetitions per model: the six new cases, two selected-exception cases, five loop-control cases, truncated input, dependent calls retaining an intermediate object, review-policy composition, and ten-child iteration.
The boolean assignment case receives ten additional repetitions per model to examine ordinary completion.
This gives 180 shared trials per compared implementation, with fresh state for every trial.

The control records its first 15 repeated cases in `control-repeat` and ten-child iteration separately in `control-iteration`.
The explicit source-selection case was added after initial screening and has three repetitions per model in `control-source-selection`; the other control programs and assertions are identical to their main-run versions.
The final treatment records all 17 repeated cases in `source-access-repeat`.
The boolean batches were fixed at ten repetitions before execution; failed cases were not retried until successful.
Separate existing provider tests retain their own models and settings and are not included in shared token totals.

The main command is:

```bash
uv run --no-sync --env-file .env pytest -q --tb=short tests/integration/ -m model_evaluation \
  --evaluation-model openai-responses:gpt-6-luna \
  --evaluation-model openai-responses:gpt-6.1-sol \
  --evaluation-settings '{"openai_reasoning_effort":"low"}' \
  --evaluation-output evals/outputs/<new-directory>
```

The repeated subset adds `--evaluation-repeat 2` and:

```text
-k 'return_preserves or default_exception or helper_selection or output_contract or application_inspect or selected_exception or loop_control or truncated_input or intermediate_object or model_selected_children or model_materializes or explicit_task_source'
```

The boolean batch uses `--evaluation-repeat 10 -k 'test_committed_values and boolean'`.
Combined provider verification removes `-m model_evaluation`, enables `NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1` and `NIGHTHAWK_OPENAI_MULTIMODAL_INTEGRATION_TESTS=1`, and writes JUnit results with `--junitxml`.
Provider-only repetition uses separate pytest processes and separate JUnit files; repeating the same file path in this pytest version collected it only once.

## Screening observations

Artifact names in this record are relative to ignored `evals/outputs/` and begin with `20261029-step-prompt-`.
The names `final` and `selected` identify intermediate experiments; the final implementation is recorded as `source-access`.

- `candidate` compacted origin labels and reduced completion guidance to 19 tokens.
  All 90 shared trials passed; the provider image-input case attempted to import unavailable `PIL` and failed.
- `final` removed routine origin labels and the special collision treatment of an application named `inspect`.
  Shared trials had two failures: Luna returned from a boolean-assignment block instead of completing with `pass`, and compared an inspection dictionary to `5` in the ten-child iteration case.
  The 60 additional selected trials passed.
  Its provider image-input case also attempted to import unavailable `PIL`.
- `selected` added the explicit normal-completion example and assignment/return distinction.
  Its full 90 shared trials passed, while native image input still attempted to import unavailable `PIL`.
  Its boolean screening passed 20/20, matching the control's 20/20.
  One of its 60 selected repetitions returned an opaque object's inspection dictionary instead of the original object.
  This reinforced the need to clarify the inspection helper's result contract.
- `refined` also specifies the inspection dictionary and directs perceptual judgments to attached media when present.
  It passed 173/174 shared trials, matching the control's count, but used 618,203 input tokens against 605,302 for the control's same 174 trials.
  Additional requests offset the shorter fixed prompt.
  Its review-policy failure used keyword matching and missed the numerical claim beginning with `Twenty`.
  Provider verification also encountered an invented `functions` import in the provided-tools smoke case, and one carry repetition parsed `10.` with `int()`.
- `semantic-screen` strengthened the distinction between meaning and mechanical computation.
  Review-policy composition passed all six fixed screening trials and carry passed all nine provider trials.
- `ondemand` also removes the initial provenance/collision listing and offers inspection for missing details.
  Luna ignored the requested task source in two of its three source-selection trials and used the visible Python value instead.
  This prevented adopting generic discovery guidance alone.
  One carry diagnostic returned 400 instead of 200; no carry-specific rule was subsequently added.
- `source-access` keeps the compact context and adds the explicit source-selection expression only when relevant names have multiple sources.
  The six fixed source-selection screening trials passed.

These observations motivated semantic clarifications, not task-specific expressions, dependency blacklists, or weaker acceptance criteria.
They do not isolate the causal effect of every wording change.

## Prompt size

For the integer assignment case, `o200k_base` counts are:

| Component | Control | Final |
| --- | ---: | ---: |
| Base system template | 205 | 218 |
| Dynamic completion guidance | 72 | 30 |
| User context and program | 172 | 148 |
| Compact JSON response schema | 212 | 167 |
| Sum | 661 | 563 |

This is 98 tokens, or 14.8%, less for the measured components.
The schema count uses `json.dumps(schema, separators=(",", ":"))` before provider normalization.
These counts exclude tool definitions, provider framing, conversation history, and native-media token charges; they are not billed-request estimates.
Larger binding sets benefit from removing the repeated per-binding origin and name lines.

## Results and decision

Retain `source-access` as the tested balance of concise presentation and explicit execution semantics.
The shared comparison has exactly matching case/model/parameter counts: 180 trials on each implementation.
The control passed 179/180 and the final version passed 180/180, with no skipped, errored, interrupted, or unattempted shared trials.
The control failure called each selected review child twice after evaluating its results without retaining them, violating the requested call count even though the final values were correct.

| Version | Model | Passed | Observed requests | Input tokens | Output tokens | Sum of trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | `gpt-6-luna` | 89/90 | 341 | 336,987 | 10,220 | 531.460 |
| Control | `gpt-6.1-sol` | 90/90 | 315 | 279,655 | 8,248 | 645.337 |
| Final | `gpt-6-luna` | 90/90 | 363 | 347,370 | 10,192 | 568.442 |
| Final | `gpt-6.1-sol` | 90/90 | 331 | 265,439 | 8,243 | 1,166.633 |

Total observed input decreased from 616,642 to 612,809 tokens, or 0.6%.
Luna's input increased 3.1%, while Sol's decreased 5.1%; more requests offset most of the fixed prompt saving.
These runs do not demonstrate lower latency or uniform workload savings.
The useful result is a smaller initial presentation with the observed boundary failures addressed, not a claim that every task becomes cheaper.
The final transcript audit found the current base template in all 256 recorded completed steps, including children.
`source-access/comparison.json` retains the aggregate counts, usage, and timings.

The final combined provider invocation passed 104 tests: 92 shared trials and 12 provider cases, with 12 coding-agent cases disabled.
Two additional fixed provider batches passed all ten cases.
Across matched repeated diagnostics, carry passed 9/9 for the final version and 8/9 for the control; native image input and tool-return images passed 6/6 and 5/6 respectively.
The control failures were a carry continuation returning 10 instead of 20 and an attempted import of unavailable `PIL`.
The earlier treatment failures remain evidence of variability; the later successes do not establish that these errors are impossible.

The control diagnostic records are `control-carry.xml`, `control-images.xml`, and `control-provider-{2,3}.xml`.
The final records are `source-access/integration.xml` and `source-access-provider-{2,3}.xml`.
All names use the `20261029-step-prompt-` prefix described above.

## Provenance and verification

All model-evaluation directories retain `manifest.json`, individual trial records, and `summary.json`.
Each source variant has a matching source archive whose contents were checked against every manifest file hash.
Archives contain source, tests, `pyproject.toml`, and `uv.lock`; they exclude credentials.

| Variant | Manifest source SHA256 |
| --- | --- |
| Control, first 174 trials | `b2d6e0b28814dc6bec92b87c9e2faca920778298071cee000295c33f2ff19921` |
| Control, added source-selection case | `50b3a6ddc8cc445fc4b47c3b401c067b2b1f1d855a1aa3fd4de1dc8d30f5e548` |
| Candidate | `7fa912843ceb373aaf38ae05b7603fab23bca9a2d1285e68f3350deed24d5559` |
| Origin-free, 19-token completion guidance (`final`) | `8bcd6f04d7579f1d76da6b16a140b8e758d3b09830e1b3e3a63db6933b80f6ee` |
| Explicit completion (`selected`) | `0ece2ab38d3a26c08603a8d66435dead7a61b5fc43ea4dd710f9df6ea0defdb2` |
| Inspection and media (`refined`) | `44a96c7df9e00177de4a965caab72fcba98a65bac3144b65551b111afc4ce8aa` |
| Semantic guidance screening | `3b451231f1843b83f884a6114c6b38531cdc6e4a2aeb7c6fa7bc033d974d79bb` |
| Discovery without source-selection expression (`ondemand`) | `7149538799b51b1c24543d42893c2b3e497188624d4ac1ef278734851e8488f4` |
| Final (`source-access`) | `50debca9467f3cd43c4682eb4fdfcd4484db6b154f583501c162852488662eee` |

Archive names use `20261029-step-prompt-<variant>-sources.tar.gz`, except the original control at `20261029-step-prompt-control-source/sources.tar`.
The additional control case uses `control-source-selection-sources.tar.gz`; the semantic screening archive uses `semantic-sources.tar.gz`.
Repeated and boolean batches share their corresponding source variant's fingerprint.
The only difference between the two control manifests is the additional `tests/integration/test_environment_selection.py` file; it does not alter existing tests or runtime code.

The final source passes the deterministic suite with 1,327 passed and 75 opt-in or environment-dependent skips in 51.68 seconds.
Ruff lint and formatting, Pyright, and strict documentation generation pass.
The deterministic suite includes the rendered documentation link checks and exact prompt examples.
Final source and test hashes match the evaluated archive.

Environment: Python 3.13.12, Nighthawk 0.17.0, pytest 9.1.1, pytest-asyncio 1.4.0, Pydantic 2.14.1, pydantic-ai-slim 2.58.0, OpenAI Python 3.35.1, MCP 2.3.0, and Claude Agent SDK 0.2.151.

## Limits

These synthetic execution tasks assess the observed values, identity, effects, and control flow; they do not establish general application reliability or a prompt-injection security boundary.
Prompt selection used the observed failures, so the evaluation is developmental rather than an independent holdout.
Small repeated samples do not establish equal failure probabilities, and provider behavior remains stochastic.
Completed-agent usage can omit failed or interrupted provider requests and is not a billing record.
Some batches ran concurrently; elapsed times describe these runs rather than an isolated latency benchmark.
Coding-agent provider tests remained disabled; their deterministic adapter tests passed.
