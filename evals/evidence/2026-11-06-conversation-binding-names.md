# Conversation binding names and the application context field

## Decision

`ConversationInput` no longer duplicates `read_binding_name_tuple` and `write_binding_name_tuple`.
`choose_conversation_step` binds the selected read and write names from `ConversationBindings`, the caller's binding contract, as control inputs.
The conversation program no longer opens with "Use the explicitly supplied context field".
It states that `context` is None unless the application supplied context values, and directs value reads to explicit read bindings or `namespace_page` items.
A Session never supplies `context`.

## Comparison protocol

The control is an immutable worktree of `0394493`.
The treatment is `57baff5` plus only this change; `57baff5` removes `EffectFootprint.namespace_read_name_set`, which does not appear in this case's prompts.
Each arm ran `tests/integration/harness/test_session.py::test_single_model_conversation_calculation_retention_and_function_call` 30 times as independent pytest processes, six at a time.
Each trial runs four Session cells: two conversational tasks, one Natural call of a Session-defined function, and one conversational call of that function.
All runs use `openai-responses:gpt-6-luna` with production defaults and unchanged assertions.
An experiment-only pytest plugin recorded Pydantic AI spans with `Agent.instrument_all(InstrumentationSettings(include_content=True))` into an in-memory OpenTelemetry exporter; it is not retained.

## Results

| Arm | Passed | Trials reading `conversation_input.context` | Tool calls | Requests | Input tokens | Output tokens | Call-phase seconds (min / mean / max) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Control | 26/30 | 24 (29 calls) | 460 | 526 | 1,170,181 | 39,321 | 5 / 41 / 50 |
| Treatment | 28/30 | 0 | 451 | 545 | 1,206,376 | 36,496 | 3 / 41 / 49 |

Usage sums all trials of an arm.
Control failures:

- Two trials read the None `context` field as a page, `sum(next(item.value for item in conversation_input.context ...))` and `sum(conversation_input.context.item_tuple[0].value)`; each expression's own `TypeError` or `AttributeError` propagated and failed the cell.
- One trial awaited the synchronous `resolve_session_function(...)`, and the expression's own `TypeError` propagated.
- One trial passed the string `'total'` instead of its value 6 to `describe`.

Treatment failures:

- One trial awaited the synchronous `query_session_data(...)`, and the expression's own `TypeError` propagated.
- One trial passed the string `'total'` to `describe`.

The treatment removed every read of the absent context field, the only failure class that the change addresses.
The pass-rate difference is two trials out of 30 and is not statistically established.
Remaining failures are expressions that raise on their own and a semantic argument error; this case does not establish general Session conversation reliability.
Codex, Claude, and other models were not run.

## Source and environment

Python 3.13.12, Nighthawk 0.19.0, Pydantic 2.14.2, Pydantic AI 2.63.0, OpenAI 3.41.0, HTTPX 0.28.1, pytest 9.2.0.

| File | Control SHA-256 | Treatment SHA-256 |
| --- | --- | --- |
| `src/nighthawk/parts/conversation/_judgment.py` | `d650103f320d0273d9d303e36f7a909b068c9e49df69b5e6fc953f6738b0a182` | `4c1da85a5a269fa6e4f55ab0a3e9de0f04efd3e17a836797501961a587999913` |
| `src/nighthawk/parts/conversation/_records.py` | `da7bdc00c14d9157d3052d5bd1476ab688648af7ac60400330c1bf2c332ea639` | `6836a7807d68fe0e180dd043100496649de7f77ae9d192d6982161a57fa69687` |
| `src/nighthawk/session/_session.py` | `7ba7fd0ca23ffaf6c40339d53ce12765bd6caeda38643d3c8aa5af7aea4d8c3a` | `694cc467b4b71f9534bc99efbab6966c898ebbf786bd9a21699e112babb9c64e` |
