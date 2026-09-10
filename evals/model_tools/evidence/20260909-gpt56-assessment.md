# GPT-5.6 tool-interface assessment, 2026-09-09

The requested GPT-5.6 Luna/Sol rerun completed 576 trials in two separate
experiments. These models handled the tool surfaces substantially more reliably
than the earlier GPT-4.1 experiments. The original three-tool proposal had the
highest observed main-study success; the refined three-tool and merged two-tool
surfaces tied in aggregate. The earlier diagnosis of widespread syntax and
completion problems should not be generalized to these models.

## Main comparison: 384 trials

Sixteen cases, three surfaces, two models, two formulations, and two repetitions
were tested with example guidance. Each cell includes control flow, values,
identity, required calls, and effects. State/effect scores equal overall scores
in this study: no failure was caused only by the completion kind.

| Model | Original three tools | Refined three tools | Merged two tools |
| --- | --- | --- | --- |
| `gpt-5.6-luna` | 58/64 (90.6%) | 54/64 (84.4%) | 55/64 (85.9%) |
| `gpt-5.6-sol` | 64/64 (100%) | 61/64 (95.3%) | 60/64 (93.8%) |
| Both models | 122/128 (95.3%) | 115/128 (89.8%) | 115/128 (89.8%) |

Across 128 matched pairs, refined won 1, tied 119, and lost 8 against original.
Merged won 2, tied 124, and lost 2 against refined. These small, correlated
samples do not establish universal superiority, and original/refined differ in
several contracts and their necessary guidance, not just the tool count.

Average admitted runtime operations per trial were 3.67 for original, 3.23 for
refined, and 3.18 for merged. Fewer operations did not yield higher success.
Successful nested-counter trials still required at least 21 runtime operations;
interaction cost remains relevant even when the model executes correctly.

The 32 failures consisted of:

- 17 helper-name collision failures and 3 ordinary collision failures. Returning
  references through inspection does not help when the model calls the shadowing
  function without discovering the intended source. The original surface
  documents qualified references upfront; this presentation difference is part
  of the comparison and prevents attributing its result to schema alone.
- 11 failures to perform the explicitly requested unbound-local read. Inspected
  traces inferred the answer from `unbound_python_names` and assigned `'unbound'`
  immediately. These are procedural omissions, not evidence that parent values
  leaked through the lookup rule. The oracle deliberately requires an attempt.
- 1 nested-counter failure that called the child an extra time. It produced the
  correct final labels but violated the exact ordered-call requirement.

All 24 mutation trials and all 24 return trials passed. Across the main study
there were only 2 admitted syntax errors, no wrong-control-only failures, and
one excess domain call (the nested child). Large values, opaque identity,
pagination/discovery, validation, approval/rejection, and partial-effect recovery
also passed in every main-study trial. See the [main report](20260909-gpt56-main.md).

## Description comparison: 192 trials

Eight cases were tested with two descriptions, three surfaces, two models,
two repetitions, and one formulation. This is a separate experiment; its task
distribution differs from the main study and overlaps its tasks.

| Surface | Short overall | Examples overall | Short state/effects | Examples state/effects |
| --- | --- | --- | --- | --- |
| Original | 32/32 | 32/32 | 32/32 | 32/32 |
| Refined | 32/32 | 30/32 | 32/32 | 32/32 |
| Merged | 31/32 | 32/32 | 32/32 | 32/32 |

Short guidance passed 95/96 and examples passed 94/96. Across 96 matched pairs,
examples won 1, tied 93, and lost 2. All three failures were Luna choosing
`return` instead of `pass` after correct state/effect handling. Sol passed all
96 trials. There were no duplicate domain calls. This near-ceiling task set
does not establish a useful description advantage. See the
[description report](20260909-gpt56-guidance.md).

## Revised design interpretation

The original three-tool surface performed best in this experiment. The ratified
refinements remain candidates with useful reference and continuation semantics,
but their cognitive advantage remains unproven. A two-tool merge is not supported
by a success advantage either. No production tool contract was changed in this
rerun.

The follow-ups from the earlier assessment should now be interpreted as follows:

- **P-TOOLS-005:** Still the strongest candidate. Known collisions and qualified
  references should be visible before the model must infer a need to inspect.
  This remains a proposal; the rerun did not implement it or isolate its effect.
- **P-TOOLS-006:** Reconsider priority. Mutation succeeded and ordered nested
  execution passed 23/24 main trials. A new statement tool is not justified as
  necessary for basic correctness by this evidence. Investigate the cost of
  materializing counters across 21 or more operations before expanding the API.
- **P-TOOLS-007:** Keep completion and duplicate-effect scenarios as regression
  coverage. Main-study return handling was reliable; three description-study
  completion errors remain. A completion redesign is not yet demonstrated to be
  necessary by these results.

In particular, preserve the distinction between accessible live values and their
prompt presentation. These results point more strongly to improving which
origins/references are initially visible than to adding more tool descriptions
or weakening runtime validation.

## Method, provenance, and limits

Both models were explicitly requested by name and successfully probed through
Chat Completions before testing. Official documentation lists `medium` reasoning
support for [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
and [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).
The experiments explicitly fixed it to `medium` for both models. No model
substitution or production-default change was made.

Both studies used exactly the same saved prototype source hashes and Pydantic AI
2.37.0. Their common limits were 8,192 completion tokens per response, a
120-second model timeout, a 300-second trial timeout, 100,000 total tokens per
trial, 45 requests, and 90 tool calls. Main and description runs used concurrency
8 and 4 respectively, with the same shuffled seed, 20260909. These are ceilings;
token usage includes provider-reported reasoning usage. Reported reasoning tokens
were 38,148 in the main study and 17,918 in the description study.

Neither completed study recorded an external exception or limit failure.
Recorded response metadata identified only the two requested models and showed
`stop` for all 2,611 recorded responses. Metadata is captured when an agent run
returns a result; terminal host rejection does not supply it. Six successful
smoke trials and two connectivity probes are excluded from the 576 trial count.
All scheduled main/description trials are included, including failures.

Historical GPT-4.1 runs used lower token/time ceilings and omitted reasoning
effort. Their main prototype also predated the syntax-preflight correction.
Therefore the numerical improvement is not a controlled model-only effect.
The new studies retain the same task semantics, prompts, model-facing tools,
and oracles as the corrected historical description prototype; runner changes
expose settings and preserve response/usage metadata and source snapshots.

These remain synthetic, development-visible tasks on one API transport. The
nested child is deterministic, and the prototype does not validate production
Python frame capture, initial-value type inference, or host ledger integration.
Repeated formulations are correlated; Wilson intervals in the generated reports
are descriptive. A 64/64 result does not establish general perfection.
Operation/error counts exclude requests rejected before workspace entry and the
structured completion tool; usage and overall outcomes include those interactions.

Reviewable evidence includes [trial metrics](20260909-gpt56-trials.csv),
[configurations](20260909-gpt56-configurations.json), and
[selected traces](20260909-gpt56-selected-traces.json). Full traces and matching
source snapshots remain locally in ignored `outputs/gpt56-main-20260909/` and
`outputs/gpt56-guidance-20260909/`. Historical results remain separately available
in the [initial assessment](20260909-assessment.md).
