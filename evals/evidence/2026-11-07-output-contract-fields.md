# Declared output fields in the Step context

## Decision and scope

The locals summary previously described an output by its contract name and, for an initialized instance, by the runtime types of its fields.
An unbound Pydantic output appeared only as `result: Ticket  # unbound`, and an initialized one as `result.priority: str = "low"`, so the model could not see `Literal` choices, element types, or nested models without inspecting the class through tool calls.
Output contracts that are or contain Pydantic models or dataclasses now list declared fields as Python field declarations, use declared annotations for an initialized instance, and describe each reachable model or dataclass type once per section.
TypedDict contracts are not expanded because reading their annotations can require evaluation on Python 3.14.

The treatment also includes the separate correction that spells the Ellipsis of `tuple[int, ...]` as `...` instead of an opaque value.
Natural program declarations, tool admission, output validation, and finalization are unchanged.

## Evaluation protocol

The control is revision `2284e51` (source fingerprint `85478998372b5458`), which adds the structured output cases without the presentation change.
The treatment is revision `d17c742` (source fingerprint `5a3a01a8b0ff6d03`), which also contains `361c86c`.
Each variant ran from a separate worktree with unchanged programs and assertions.

- All 55 model-evaluation cases ran once per model, including the four new cases of `test_structured_output_contracts`.
- The four structured cases then ran two more repetitions per model, so each structured case has three trials per model.
- Models: `openai-responses:gpt-6-luna` and `openai-responses:gpt-6.1-sol`, with production default settings.
- Versions: Python 3.13.12, Pydantic 2.14.2, Pydantic AI Slim 2.63.0, OpenAI SDK 3.41.0, pytest 9.2.0.
- Started 2026-11-07 between 16:35 and 16:50 UTC; the control and treatment runs overlapped in time.

The structured cases cover an uninitialized model with `Literal` fields, a model holding a tuple of nested models, a dataclass with a `Literal` field, and an initialized model output that the program asks the model to update.

## Results

| Cases | Control | Declared fields |
| --- | ---: | ---: |
| Structured output cases | 19/24 | 24/24 |
| Initialized model output | 1/6 | 6/6 |
| Other structured cases | 18/18 | 18/18 |
| Other shared cases | 102/102 | 102/102 |

All five control failures are `initialized-model` trials, two with Luna and three with Sol.
In each, the model changed `result.priority` in place to `"critical"`, which is outside `Literal["low", "high"]`, and final output validation rejected the instance.
The control context presented the field as `result.priority: str = "low"`.

Completed-agent usage for the structured cases:

| Variant | Model | Passed | Requests | Input tokens | Output tokens | Trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | Luna | 10/12 | 91 | 188,471 | 8,734 | 192.5 |
| Declared fields | Luna | 12/12 | 41 | 36,009 | 3,757 | 84.6 |
| Control | Sol | 9/12 | 59 | 54,948 | 2,227 | 109.1 |
| Declared fields | Sol | 12/12 | 33 | 23,298 | 1,035 | 51.3 |

Across both models, structured-case requests decrease from 150 to 74 and input tokens from 243,419 to 59,307.
Restricted to the three cases that passed in every trial of both variants, Luna requests decrease from 80 to 32 and input tokens from 178,667 to 28,432; Sol requests decrease from 49 to 24 and input tokens from 47,964 to 16,956.
In the structured-case transcripts, control models made 73 `nh_evaluate` calls, 28 of which inspected types through expressions such as `model_fields`, `__annotations__`, `fields(...)`, or `type(...)`, in 13 of 24 trials.
The treatment made 12 `nh_evaluate` calls, one of which inspected a type, in one trial.
The classification matches those expression patterns in recorded transcripts and is not a semantic review of every call.

The other 51 shared cases pass in both variants.
Their Luna usage changes from 165 requests and 145,260 input tokens to 155 and 125,388; Sol changes from 168 requests and 138,376 input tokens to 168 and 137,995.
These cases do not use structured outputs and ran once per model, so the difference is not attributed to the change.

## Artifacts and verification

Run directories under ignored `evals/outputs/`:

- `20261107-output-contract-control-shared` and `20261107-output-contract-control-structured-repeat`.
- `20261107-output-contract-treatment-shared` and `20261107-output-contract-treatment-structured-repeat`.

Deterministic coverage is in `tests/adapters/pydantic_ai/test_prompt_annotations.py` and `tests/core/test_description.py`; the structured cases are in `tests/integration/test_model_behavior.py`.

## Interpretation and limits

The cases are synthetic and small; three trials per case and model do not establish reliability for other contracts or applications.
Usage is completed-agent usage, not a billing record.
The initialized-model failures depend on the model choosing an out-of-contract value; the treatment shows the declared choices but does not prevent such a choice, and final validation still rejects it.
The treatment includes the Ellipsis spelling correction, which affects any context that shows a variadic tuple annotation.
