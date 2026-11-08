# Python notation for values and tool results

## Decision and scope

The Step context previously reduced each value with fixed limits (strings to 160 characters, 8 items, depth 2) and then summarized the JSON projection with headson when it exceeded the configured token budget.
Instances of named tuples, dataclasses, and Pydantic models were shown as field mappings, the configured `value_max_tokens` did not apply to strings, and headson omissions did not state the amount or the position of omitted items.
Successful tool results on provider-backed tool loops were sent as complete Pydantic JSON without a token bound; headson applied only to error previews, coding-agent transport fallbacks, and traces.

Values in the Step context and tool results on every transport now use Python notation: literals, and constructor form for declared-field instances.
The most detailed rendering that fits the configured token budget is selected; omission markers state the amount and, when the position has a Python path, the expression that reads it.
Within one context construction or tool result, an object shown again is written as the path where it was first displayed, which also represents cycles.
A path-like `nh_evaluate` expression is the path of its result, top-level text results stay raw text, and failures are reported as `error = {...}`.
The default system prompt gains one line that states the notation and the tool-result budget; the text-projected preview fragment is removed.
`json_renderer_style` and the headson dependency are removed, and `StepContextLimits.value_max_depth` is added.

## Evaluation protocol

The control is revision `5e9943c` (source fingerprint `43016eab7258f3e4`): revision `2eded0e` with the case commit `f923aa8` cherry-picked, without the presentation changes of `092bd3b` and `bd59e1a`.
The treatment is revision `f923aa8` (source fingerprint `b331996db8d9f6df`), which contains `bd59e1a` and the same cases.
Each variant ran from a separate worktree with unchanged programs and assertions.

- All 61 model-evaluation cases ran once per model.
- The six focused cases then ran two more repetitions per model, so each focused case has three trials per model: the four nested structured-input cases of `2eded0e`, a case that counts distinct nested objects by identity when equal-valued objects are present, and a case that reads a fact after the opening of a multi-line text input.
- Models: `openai-responses:gpt-6-luna` and `openai-responses:gpt-6.1-sol`, with production default settings and context limits.
- Versions: Python 3.13.12, Pydantic 2.14.2, Pydantic AI Slim 2.63.0, OpenAI SDK 3.41.0, pytest 9.2.0.
- Started 2026-11-08 at 12:16 and ended by 12:29 UTC; the control and treatment runs overlapped in time.

## Results

| Cases | Control | Python notation |
| --- | ---: | ---: |
| Focused cases | 36/36 | 36/36 |
| Other shared cases | 110/110 | 110/110 |

Completed-agent usage for the focused cases (three trials per case and model):

| Variant | Model | Passed | Requests | Input tokens | Output tokens | `nh_evaluate` calls | Trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Control | Luna | 18/18 | 46 | 34,592 | 2,042 | 10 | 70.3 |
| Python notation | Luna | 18/18 | 36 | 27,623 | 1,083 | 0 | 56.4 |
| Control | Sol | 18/18 | 54 | 48,829 | 1,588 | 18 | 82.3 |
| Python notation | Sol | 18/18 | 36 | 27,564 | 1,023 | 0 | 56.1 |

Every treatment trial of the focused cases finished with one assignment and the final output, without inspection calls.
In the control, every multi-line text trial first evaluated the whole text, which the context had cut at 160 characters.
Control trials of the structured and identity cases inspected the nested values with expressions such as `vars(item)`, `dir(t)`, or `type(m).__name__` before assigning: Luna in 7 of 15 such trials and Sol in all 15.
No treatment trial used subscript access on an instance; the earlier mapping display of `092bd3b` led Sol to subscript nested instances in 12 of 12 trials.
In the identity case, both variants counted objects with `id(...)`; the treatment context showed the shared accounts as paths such as `transfers[0].target` and the equal-valued account in full.

The other 55 shared cases ran once per model.
Luna changes from 180 requests and 159,873 input tokens to 176 and 177,141; Sol changes from 181 requests and 148,196 input tokens to 176 and 157,248.
The new system prompt line adds 67 tokens to every request, about 11,800 input tokens over 176 requests per model.
The largest per-case increase is `test_model_materializes_parent_counter_before_ten_real_children` (51 requests in both variants), whose growing parent history repeats the longer prompt and tool results.
These cases ran once per model, so smaller differences are not attributed to the change.

The four runs used about 781,000 completed-agent input tokens.

## Artifacts and verification

Run directories under ignored `evals/outputs/`:

- `20261108-python-notation-control-shared` and `20261108-python-notation-control-focused-repeat`.
- `20261108-python-notation-treatment-shared` and `20261108-python-notation-treatment-focused-repeat`.

Deterministic coverage is in `tests/core/test_description.py`, `tests/adapters/pydantic_ai/test_rendering_values.py`, `tests/adapters/pydantic_ai/test_prompt_annotations.py`, `tests/adapters/pydantic_ai/tools/test_rendering.py`, `tests/adapters/pydantic_ai/tools/test_execution.py`, and `tests/adapters/pydantic_ai/tools/test_discovery_content.py`; the focused cases are in `tests/integration/test_model_behavior.py`.

## Interpretation and limits

The cases are synthetic and small; three trials per case and model do not establish reliability for other values or applications.
Usage is completed-agent usage, not a billing record, and the run records do not separate cached input tokens.
No case exercises a tool result that reaches `tool_result_max_tokens`, so the effect of bounding provider-backed tool results, which were previously sent in full, is not measured here.
The default budgets still bound context values to 200 tokens; values that exceed them are cut with continuation markers instead of being summarized.
