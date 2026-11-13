# Structured assignment tools for model and dataclass outputs

## Decision and scope

A step exposes one built-in tool, `nh_assign_<name>`, for each declared output whose contract is a Pydantic model or dataclass.
The tool's argument schema is derived from the contract, and the assignment passes the same operation boundary and validation as `nh_assign`.
The step system prompt gains one line that tells the model to write a new value of such an output with that tool and to keep `nh_assign` for a value that Python computes or already holds.
The tool result names the target and how it was retained, without repeating the value.
The tool is sent as strict when closed objects with every property required and typed array items can express the contract.

Before the change, a model wrote every output as a Python expression inside the JSON string argument of `nh_assign`.
A large nested value in that form needs two layers of escaping, and a syntax error discards the whole expression.
Natural program declarations, `nh_assign`, `nh_evaluate`, final validation, and scoped tools are unchanged, except that scoped tool names starting with `nh_assign_` are rejected.

## Evaluation protocol

Each variant ran from its own worktree with unchanged programs.

| Variant | Source | Difference |
| --- | --- | --- |
| Control | `86a9dfa` (source fingerprint `6cf53c7360557dcf`) | The evaluation cases without the tools. |
| Structured assignment | `48f837b` (source fingerprint `5d895d3504ee4f08`) | The change. |
| Without strict | `48f837b` with every structured assignment tool sent with `strict=False` (source fingerprint `e289faf3bdad4440`) | Isolates provider-constrained generation. |

- Structured cases: the four cases of `test_structured_output_contracts` and the new `test_large_authored_structured_output`, three trials per model for every variant.
  The new case asks for a model holding twelve nested entries; each entry copies a rule that contains quotes, brackets, backslashes, and Japanese text, and adds one Japanese sentence that the model writes.
- Shared suite: all 66 model-evaluation cases once per model, for the control and the change.
  The control's shared run used `4192060` (source fingerprint `5dbb685ed2d950d2`), which differs from `86a9dfa` only in one assertion of the new case: it rejected a copied rule that kept the space after its marker.
- Models: `openai-responses:gpt-6-luna` and `openai-responses:gpt-6.1-sol`, with production default settings.
- Versions: Python 3.13.12, Pydantic 2.14.2, Pydantic AI Slim 2.63.0, OpenAI SDK 3.41.0, pytest 9.2.0.
- Started 2026-11-13 between 14:46 and 15:02 UTC; the runs overlapped in time.

## Results

Structured cases, three trials each:

| Variant | Model | Passed | Requests | Input tokens | Output tokens | Trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | Luna | 15/15 | 61 | 84,755 | 10,856 | 224.7 |
| Structured assignment | Luna | 15/15 | 34 | 38,207 | 4,115 | 97.9 |
| Without strict | Luna | 14/15 | 35 | 40,716 | 4,346 | 106.6 |
| Control | Sol | 15/15 | 48 | 58,831 | 3,279 | 178.9 |
| Structured assignment | Sol | 15/15 | 34 | 39,808 | 3,687 | 133.3 |
| Without strict | Sol | 15/15 | 34 | 39,975 | 3,669 | 143.1 |

Tool calls in the structured-case transcripts:

| Variant | Model | `nh_assign` calls | Failed `nh_assign` calls | `nh_assign_result` calls | Failed `nh_assign_result` calls | `nh_evaluate` calls |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | Luna | 29 | 17 | - | - | 19 |
| Structured assignment | Luna | 0 | 0 | 15 | 0 | 4 |
| Without strict | Luna | 0 | 0 | 15 | 0 | 5 |
| Control | Sol | 27 | 9 | - | - | 6 |
| Structured assignment | Sol | 2 | 0 | 14 | 0 | 3 |
| Without strict | Sol | 2 | 0 | 14 | 0 | 3 |

The control passes every trial, but 26 of its 56 assignments fail and are repeated.
With the tools, both models write the output through `nh_assign_result` without a program instruction, and no assignment fails.
Across both models, structured-case requests decrease from 109 to 68, and input tokens from 143,586 to 78,015.
Sol's output tokens rise from 3,279 to 3,687: in the large case the control computed the copied rules with a Python expression over the input, while the change writes them as arguments.

The large case alone:

| Variant | Model | Passed | Requests | Input tokens | Output tokens | Trial seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Control | Luna | 3/3 | 17 | 44,241 | 7,443 | 111.4 |
| Structured assignment | Luna | 3/3 | 10 | 18,405 | 3,255 | 43.9 |
| Without strict | Luna | 2/3 | 11 | 20,887 | 3,459 | 47.8 |
| Control | Sol | 3/3 | 15 | 33,102 | 2,228 | 76.6 |
| Structured assignment | Sol | 3/3 | 10 | 20,278 | 3,099 | 75.8 |
| Without strict | Sol | 3/3 | 10 | 20,445 | 3,081 | 83.8 |

The single failure without strict is a Luna trial that reported five MUST rules instead of six while listing all twelve rules correctly; it is a judgment error, not a malformed value.

Shared suite, one trial per case and model:

| Variant | Model | Passed | Requests | Input tokens | Output tokens |
| --- | --- | ---: | ---: | ---: | ---: |
| Control | Luna | 66/66 | 220 | 271,912 | 10,666 |
| Structured assignment | Luna | 66/66 | 209 | 258,193 | 8,737 |
| Control | Sol | 65/66 | 211 | 246,749 | 6,205 |
| Structured assignment | Sol | 66/66 | 211 | 260,235 | 6,077 |

The control's Sol failure is the large case under the stricter assertion of `4192060`; the copied rule kept the space after its marker.
The 61 cases that declare no model or dataclass output pass in both variants, with 198 and 197 requests for Luna and 197 and 198 for Sol.

## Strict tool definitions

The variants with and without strict do not differ in these cases: no assignment failed in either, and usage is within a few percent.
The evaluation therefore does not show a cost of strict, and it does not show that provider-constrained generation is what removes the failures; removing the expression string is sufficient here.
Strict stays enabled where it can express the contract because it makes argument syntax a provider mechanism instead of a model behavior, and contract conformance is established by assignment validation in both cases.

An application observation remains unexplained.
In an application whose step returns a large work-state update, a prototype of the tool with strict produced one kind of optional human-facing item less often than the control, and three trials without strict did not show the reduction.
The sample was three trials per variant, and the application now derives that item in host code.

## Tool definitions and the provider prompt cache

Two direct measurements on `gpt-6-luna` through the Responses API, one run each on 2026-11-13, inform the roadmap entry on typed value tools loaded on demand.
Both used about 14,500 tokens of instructions and two function tools.

- Appending a third function tool to an otherwise identical request reduced cached input tokens from 14,505 to 0.
  Extending only the messages kept 14,505 cached tokens.
- A third function tool declared with `defer_loading` beside the provider's `tool_search` tool was found and called by the model within one response, and the next request reported 15,923 cached tokens of 15,975 input tokens.

The measurements do not establish whether a tool loaded this way is generated under strict constraints.

## Artifacts and verification

Run directories under ignored `evals/outputs/`:

- `20261113-structured-assignment-control-structured-2` and `20261113-structured-assignment-control-shared`.
- `20261113-structured-assignment-treatment-structured` and `20261113-structured-assignment-treatment-shared`.
- `20261113-structured-assignment-nonstrict-structured`.
- `20261113-structured-assignment-control-structured`, the structured cases at `4192060`, whose three Sol failures of the large case led to the assertion change.

Deterministic coverage is in `tests/adapters/pydantic_ai/tools/test_structured_assignment.py`, `tests/core/test_assignment.py`, and `tests/harness/test_operation_recovery.py`; the cases are in `tests/integration/test_model_behavior.py`.

## Interpretation and limits

The cases are synthetic; three trials per case and model do not establish reliability for other contracts or applications.
Only OpenAI Responses models ran; coding-agent backends receive the same tools through MCP, where strict has no effect, and were not evaluated.
The request and token reductions come from one large case and four small ones, and their size depends on how often a model fails an expression assignment for a given contract.
The cache measurements are single runs against one model and are not part of the pytest suite.
