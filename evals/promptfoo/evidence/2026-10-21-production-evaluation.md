---
date: 2026-10-21
decision: Evaluate the production runtime directly and require evidence of requested values and effects.
---

# Production evaluation boundary

The provider at revision `c0226b8` replaced the production tool registry with synchronous evaluation-only tools, including an obsolete assignment description.
It also retained private suffix monkeypatching and prompt variants last assessed in April.
The default prompt file duplicated the production template; the coding-agent prompt included statement syntax that the expression tools do not accept.
Those configurations assessed a substituted runtime rather than the package's current default behavior.

The evaluation now supplies ordinary `StepExecutorConfiguration` fields and invokes `execute_step_async` with production prompts and tools.
Model/provider comparisons share one fixture configuration.
Prompt experiments can override the public configuration explicitly; regression runs do not replace internal modules.

## Removed experiment assets

- Legacy, sequenced, mutation-aware, and per-outcome example variants have no current experiment or reevaluation within the workspace's retention window.
  Their dated assessments remain the evidence for the runs that used them.
- The default prompt copy is redundant with `StepPromptTemplates`.
- The coding-agent prompt is a separate unverified treatment and contains unsupported statement examples.
- Prompt/suffix A/B and duplicate smoke/backend configurations retain expired treatments or duplicate the common fixture list.
- The generic promptfoo research tutorial duplicates upstream documentation and contains an unimplemented provider design.
  Contributor instructions and links to upstream documentation own operational guidance.

## Assessment corrections

The previous binding assertion accepted a missing binding as an expected null and replaced an explicit null with a namespace value.
Deterministic probes reproduced both false positives before modification.
Assertions now distinguish missing keys, committed nulls, and explicitly selected namespace effects.
Return cases check resolved values; injection cases check the complete requested result.
Function cases inspect recorded fixture invocations as well as returned bindings.
The unrestricted "something appropriate" case is removed because setting any value did not assess task quality.

These checks concern execution and fixture compliance.
They do not establish application-level judgment quality, construction effort, or a comparative advantage over ordinary Python.

## Measured runs

Both runs used `openai-responses:gpt-5.6-luna`, promptfoo 0.124.1, Python 3.13.12, `pydantic-ai-slim` 2.58.0, Pydantic 2.14.1, and OpenAI Python 3.35.1.
The editable Nighthawk package reports version 0.17.0.
No model-specific settings were supplied, caching was disabled, and concurrency was four.

| Run | Evaluation Id | Cases | Repeats | Pass | Fail | Error | Requests | Input tokens | Output tokens | Wall time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Previous adapter at `c0226b8` | `eval-rHj-2026-10-21T10:28:30` | 52 | 1 | 52 | 0 | 0 | 108 | 82,963 | 3,991 | 44.752 s |
| Production adapter | `eval-TbT-2026-10-21T10:33:41` | 53 | 3 | 159 | 0 | 0 | 311 | 237,904 | 12,112 | 121.160 s |

The previous adapter ran from an isolated copy of its files while the workspace was edited.
The production run used:

```bash
promptfoo eval --filter-providers '^openai-responses:' --repeat 3 -o outputs/20261021-production.json
```

The production evaluation-source SHA-256 is `de42338d2766fbef9c4dfa36a3750fcc2d59ede0d0e382bd215161be1ad27cbe`.
It hashes the sorted relative paths and bytes of `provider.py`, `promptfooconfig.yaml`, `assertions/*.py`, and `test_cases/*.yaml`, with a NUL after each path and each file's bytes.
The recorded baseline revision identifies the unchanged Core source.

All 53 cases appear exactly three times in the production report, under the selected OpenAI provider only.
The six function-invocation cases produced the required calls in all 18 trials, including the awaited asynchronous helper.
Median case latency was 2.834 seconds.
Usage counts are completed-agent aggregates; they are not billing records or observations of every underlying HTTP attempt.

The case sets and oracles differ: the production suite adds the Japanese loop instruction and asynchronous helper, removes the unconstrained output case, and strengthens value/effect checks.
The table therefore establishes a working production baseline, not a controlled prompt-quality or resource-efficiency comparison.
The previous 52/52 result does not repair its demonstrated assertion false positives.
No live coding-agent evaluation was performed; those provider entries remain available for environments with their executables and credentials.
Three repeats of small synthetic cases do not establish broad application reliability.

## Deterministic verification

- 30 adapter/assertion tests pass, covering missing/null outputs, namespace-effect selection, resolved returns, required call evidence, production tool descriptions and prompts, asynchronous helpers, concurrent scope separation, cancellation propagation, and effects/usage after failed validation.
- The complete Python 3.13 suite passes: 1,378 passed, 35 opt-in/version-dependent skips.
- All 97 evaluation tests also pass on Python 3.14 and 3.15.
- Ruff lint and formatting, Pyright, and strict MkDocs build pass.

The provider's async entry point and assertion context follow the upstream [Python provider](https://www.promptfoo.dev/docs/providers/python/) and [Python assertion](https://www.promptfoo.dev/docs/configuration/expected-outputs/python/) contracts.
