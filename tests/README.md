# Test organization

Tests protect current observable contracts: Python owns control flow and effects, Natural supplies judgment, and validated results cross explicit boundaries.
Choose a test owner by the behavior that would break, including when a test calls it through the public `nighthawk` namespace.

| Path | Owner and scope |
| --- | --- |
| `test_composition.py`, `test_configuration.py`, `test_function_call.py`, `test_json_renderer.py`, `test_testing.py` | Corresponding top-level library modules. |
| `natural/` | Source extraction, transformation, lexical bindings, decorators, and Python introspection. |
| `runtime/` | Run and scope configuration, context and prompt construction, discovery, executor requests, validation, operations, usage, tracing, and composed Python workflows. |
| `oversight/` | Host inspection, rejection, and validated rewrites of step commits and return expressions. |
| `lifecycle/` | Started, operation, and terminal notifications; assignment completion, interruption, and host ledger delivery. |
| `tools/` | Tool declarations, admission and ordering, evaluation and assignment, and provider-facing result normalization. |
| `backends/` | Backend settings and protocols, text and MCP projection, callback transport, and process cleanup using local stubs. |
| `resilience/` | Retry, fallback, voting, timeout, circuit breaker, budget, and their composition. |
| `docs/` | Published examples executed or compared against actual runtime output, navigation, anchors, and API inventories. |
| `evals/model_tools/` | Deterministic checks of the evaluation workspace prototype, study treatments, and semantic oracles. |
| `integration/` | Opt-in tests using real providers or coding agent executables. |
| `support/` | Shared executors, contexts, prompt helpers, tool fixtures, and backend stubs; never collected as tests. |

Within `runtime/`, use these owners:

- `test_bindings.py`: independent declaration, inference, and value validation contracts.
- `test_step_definition.py`: immutable reusable declarations and per-invocation contract selection.
- `test_preparation.py`: namespace and annotation acquisition before executor admission, including failure diagnostics.
- `test_embedded.py`: explicit host entry points, environment selection, Python composition, and writer adaptation.
- `test_runner.py`: executing and finalizing resolved steps, output selection, and Python control results.
- `test_environment.py`: name resolution, precedence, and live or inherited value views, including application names resembling generated helpers.
- `test_prompt_values.py`: value presentation in prompt context, including those application names.
- `test_step_executor.py`: agent adaptation, scoped tool delivery, prompt content, and completed usage forwarding.
- `test_decisions.py`: OpenAI Decisions through a local SDK transport, typed contracts, full evidence, scoped models, failures, and per-step details.
- `test_model_selection.py`: model/configuration resolution, executor policy, and restoration across function and scope boundaries.
- `test_model_candidate.py`: resolving model-facing return and exception expressions, including admission before evaluation.
- `test_discovery.py`: live references, name and member discovery, and environment presentation.
- `test_description.py`: best-effort metadata discovery without evaluating application annotations.
- `test_expression.py`: expression evaluation and awaitable handling, independent of tool projection.

Commit inspection belongs in `oversight/`, including embedded output replacement and rejection.
Assignment failure, cancellation, and terminal delivery belong in `lifecycle/`, including host writers.
Test pure binding rules once in their owner and keep entry-point tests focused on their integration.
Tool-result rendering and response budgets belong in `tools/test_contracts.py`; nested discovery content belongs in `tools/test_discovery_content.py`.
Provider toolset execution belongs in `tools/test_execution.py`, and pure JSON projection belongs in `test_json_renderer.py`.
Backend process termination and reaping belong in `backends/test_process_management.py`; callback transport belongs in `backends/test_tool_transport.py`.
Use `nighthawk.testing` for resolved Python values and exception classes.
`ScriptedExecutor` fails on an unexpected invocation after its responses are exhausted; supply an explicit `default_response` only when repeated responses are intended.
Use `ModelResponseExecutor` from `support/executors.py` when a test must exercise model-facing expression resolution, including operation inspection or notification ordering.
`StubExecutor` is reserved for tests that consume an envelope embedded in Natural text.
Asynchronous API tests await directly; the shared `execute` fixture adapts only the synchronous entry to the async test harness.

Keep a helper beside its sole consumer; move it to `support/` when several test modules need it.
Do not import another `test_*.py` module for helpers.
Use `conftest.py` for fixtures that pytest injects, and let pytest own collection and execution.
User-facing example code lives in docs and is executed by extraction tests in `tests/docs/`.
`docs/test_links.py` builds the site and verifies internal links and anchors in rendered HTML, using MkDocs' actual Markdown and API rendering.
Runtime test fixtures live in `tests/support/`; they must not import the published examples.
Set `NIGHTHAWK_DISTRIBUTION_PYTHON` to a fresh environment's Python executable when running `tests/docs/test_runtime_examples.py` to verify the installed wheel with those same documented examples.

Retain failure cases that enforce current boundaries, such as rejected effects, invalid outputs, scope isolation, and cancellation.
Remove checks whose only purpose is to remember deleted APIs, obsolete paths, exact documentation prose, or behavior already covered by the same owner.
Do not duplicate a dependency's own tests unless exercising a Nighthawk adapter contract.

Run the deterministic suite with `uv run pytest -q`.
Backend contract tests may start local HTTP servers and subprocesses, but do not require provider credentials.
See [CONTRIBUTING.md](../CONTRIBUTING.md#integration-tests) for opt-in provider tests.
