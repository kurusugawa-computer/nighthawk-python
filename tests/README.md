# Test organization

Tests protect current observable contracts: Python owns control flow and effects, Natural supplies judgment, and validated results cross explicit boundaries.
Choose a test owner by the behavior that would break, including when a test calls it through the public `nighthawk` namespace.

| Path | Owner and scope |
| --- | --- |
| `test_composition.py`, `test_configuration.py`, `test_json_renderer.py`, `test_testing.py` | Corresponding top-level library modules. |
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

Keep a helper beside its sole consumer; move it to `support/` when several test modules need it.
Do not import another `test_*.py` module for helpers.
Use `conftest.py` for fixtures that pytest injects, and let pytest own collection and execution.
User-facing example code lives in docs and is executed by extraction tests in `tests/docs/`.
Runtime test fixtures live in `tests/support/`; they must not import the published examples.
Set `NIGHTHAWK_DISTRIBUTION_PYTHON` to a fresh environment's Python executable when running `tests/docs/test_runtime_examples.py` to verify the installed wheel with those same documented examples.

Retain failure cases that enforce current boundaries, such as rejected effects, invalid outputs, scope isolation, and cancellation.
Remove checks whose only purpose is to remember deleted APIs, obsolete paths, exact documentation prose, or behavior already covered by the same owner.
Do not duplicate a dependency's own tests unless exercising a Nighthawk adapter contract.

Run the deterministic suite with `uv run pytest -q`.
Backend contract tests may start local HTTP servers and subprocesses, but do not require provider credentials.
See [CONTRIBUTING.md](../CONTRIBUTING.md#integration-tests) for opt-in provider tests.
