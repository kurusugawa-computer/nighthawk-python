# Remove the namespace read set from effect footprints

## Decision

`EffectFootprint` no longer has `namespace_read_name_set`.
Dispatch already rejects generated source whose free reads are not declared dependencies or builtins, so `dependency_tuple` owns which shared names a proposal reads.
The read set was recorded in dispatch events and exposed through the `execute_code` request type, but no mechanism checked it, and reading has no effect for the [graduated enforcement tiers](../../docs/agent/architecture.md#graduated-enforcement) to govern.
The class docstring, which enters the model-visible JSON schema, now describes only `namespace_write_name_set` among namespace fields.

## Comparison protocol

The control is an immutable worktree of `0394493`; the treatment is that revision with only the field and its docstring sentence removed.
Each arm ran five independent pytest processes, three at a time, over the four live cases that construct code proposals or effect footprints:

| Case | Model-facing use |
| --- | --- |
| `tests/integration/harness/test_exploration.py::test_explore_completes_with_a_typed_result` | `execute_code` with an `EffectFootprint` argument |
| `tests/integration/harness/test_reference_applications.py::test_live_generate_once_and_reuse_in_fresh_batch_runs` | Generation judgment constructing a `CodeProposal` |
| `tests/integration/harness/test_reference_applications.py::test_live_procedure_discovery` | Procedure catalog descriptors containing footprints |
| `tests/integration/harness/test_session.py::test_generated_function_declaration_python_call_and_natural_reuse` | Session function declaration through generation |

All runs use `openai-responses:gpt-6-luna` with production defaults, unchanged test assertions, and fresh state per process.
An experiment-only pytest plugin recorded Pydantic AI spans with `Agent.instrument_all(InstrumentationSettings(include_content=True))` into an in-memory OpenTelemetry exporter; it is not retained.

## Results

| Case | Control | Treatment |
| --- | --- | --- |
| Exploration | 5/5; 27 requests, 39,919 input and 3,159 output tokens, 74 s | 5/5; 32 requests, 47,428 input and 4,018 output tokens, 93 s |
| Retained text workflow | 5/5; 15 requests, 21,607 input and 2,074 output tokens, 45 s | 5/5; 15 requests, 21,095 input and 1,820 output tokens, 41 s |
| Procedure discovery | 5/5; 15 requests, 12,645 input and 876 output tokens, 34 s | 5/5; 15 requests, 12,609 input and 900 output tokens, 32 s |
| Session function declaration | 5/5; 114 requests, 256,747 input and 9,865 output tokens, 282 s | 5/5; 117 requests, 263,957 input and 9,720 output tokens, 281 s |

Usage and time are sums over five trials; time is the pytest call phase.
Model-authored tool arguments mentioned an effect footprint in 17 of 121 control calls and 20 of 128 treatment calls; none set `namespace_read_name_set` or `namespace_write_name_set`.
In the 30-trial Session conversation baseline recorded on the same control revision, none of 460 tool calls mentioned either namespace set.
The only treatment tool errors were four recoverable output-target corrections in two exploration trials (`dispatch_result` without a write declaration and `<:...>` written inside `target_path`); both trials passed, and the errors do not involve footprints.

Five trials per case can detect only large regressions; equal pass rates do not establish equivalence.
The cases exercise generated-source composition, not security isolation; Codex, Claude, and other models were not run.

## Source and environment

Python 3.13.12, Nighthawk 0.19.0, Pydantic 2.14.2, Pydantic AI 2.63.0, OpenAI 3.41.0, HTTPX 0.28.1, pytest 9.2.0.

| File | Control SHA-256 | Treatment SHA-256 |
| --- | --- | --- |
| `src/nighthawk/harness/perimeter.py` | `d2ed588e48412e8cd2b898021381fed6a49f6aa47b687b9967d48272734387b6` | `0058b5eafd1659c0398f070daeda6dbd564655c76b76f907b8837ac74e6afc90` |
