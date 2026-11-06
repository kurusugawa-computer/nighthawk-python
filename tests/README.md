# Test organization

Tests protect current observable contracts: Python owns control flow and effects, Natural supplies judgment, and validated results cross explicit boundaries.
Choose a test owner by the behavior that would break, including when a test calls it through the public `nighthawk` namespace.

| Path | Owner and scope |
| --- | --- |
| `core/` | Natural transformation, binding and operation contracts, run/scoped execution, lifecycle, oversight, and deterministic executor support. |
| `adapters/` | Pydantic AI model execution, prompts, provider transports, tools, and concrete filesystem/HTTP/storage behavior. |
| `harness/` | Authority, source admission, generated execution, resource accounting, exact evidence, and termination. |
| `parts/` | Composable judgments, domain capabilities, bounded evidence and value selection, learning, and voting. |
| `session/` | Cells, persistent bindings, operator controls, history, publication, and conversation integration. |
| `test_public_api.py` | Current declared exports and dependency direction. |
| `docs/` | Published examples executed or compared against actual runtime output, navigation, anchors, and API inventories. |
| `distribution/` | Installed wheel contracts for Core, Harness, Parts, adapters, and Session, including exported types and decorator signatures through `py.typed`. |
| `test_model_evaluation.py` | Model-evaluation selection, trial isolation, failure/interruption accounting, and evidence recording through actual pytest subprocesses. |
| `integration/` | Opt-in tests using real providers or coding agent executables, including repeated model-behavior and Python-composition evaluations. |
| `examples/` | Verified reference applications, including terminal process and presentation behavior. |
| `support/` | Shared executors, contexts, prompt helpers, tool fixtures, and backend stubs; never collected as tests. |

Model behavior and Natural composition use the `model_evaluation` fixture and explicit `--evaluation-model` selection.
The fixture records repeated trials through `tests/support/model_evaluation.py`; see [contributor commands](../CONTRIBUTING.md#model-evaluations-with-pytest).
Provider-specific transport and feature smoke tests retain their documented environment gates.

Execution and adapter tests use these behavior owners:

- `core/test_bindings.py`: independent declaration, inference, and value validation contracts.
- `core/test_step_definition.py`: immutable reusable declarations and per-invocation contract selection.
- `core/test_preparation.py`: namespace and annotation acquisition before executor admission, including failure diagnostics.
- `core/test_embedded.py`: explicit host entry points, environment selection, Python composition, and writer adaptation.
- `core/test_runner.py`: executing and finalizing resolved steps, output selection, and Python control results.
- `core/test_environment.py`: name resolution, precedence, and live or inherited value views, including application names resembling generated helpers.
- `adapters/pydantic_ai/test_prompt_values.py`: value presentation in prompt context, including those application names.
- `adapters/pydantic_ai/test_step_executor.py`: agent adaptation, scoped tool delivery, prompt content, and completed usage forwarding.
- `core/test_scoping.py`: run and scope identities, executor installation, usage-meter inheritance, and restoration.
- `adapters/pydantic_ai/test_decisions.py`: OpenAI Decisions through a local SDK transport, typed contracts, full evidence, scoped models, failures, and per-step details.
- `adapters/pydantic_ai/test_model_selection.py`: model/configuration resolution, executor policy, and restoration across function and scope boundaries.
- `adapters/pydantic_ai/test_model_candidate.py`: resolving model-facing return and exception expressions, including admission before evaluation.
- `core/test_discovery.py`: live references, name and member discovery, and environment presentation.
- `core/test_description.py`: best-effort metadata discovery without evaluating application annotations.
- `core/test_expression.py`: expression evaluation and awaitable handling, independent of tool projection.

Commit inspection belongs in `core/oversight/`, including embedded output replacement and rejection.
Assignment failure, cancellation, and terminal delivery belong in `core/lifecycle/`, including host writers.
Test pure binding rules once in their owner and keep entry-point tests focused on their integration.
Core configuration value validation belongs in `core/test_configuration.py`, adapter configuration in `adapters/pydantic_ai/test_configuration.py`, and executor construction in `adapters/pydantic_ai/test_step_executor.py`.
Function interception belongs in `core/test_calls.py`; decision syntax belongs in `core/natural/test_blocks.py`, and scripted decision recording belongs in `core/test_testing.py`.
Tool execution, discovery content, rendering, and response budgets belong in `adapters/pydantic_ai/tools/`; pure JSON projection belongs in `adapters/pydantic_ai/test_rendering_values.py`.
Backend process termination, reaping, and callback transport belong in `adapters/pydantic_ai/models/`.
Use `nighthawk.core.testing` for resolved Python values and exception classes.
`ScriptedExecutor` fails on an unexpected invocation after its responses are exhausted; supply an explicit `default_response` only when repeated responses are intended.
Use `ModelResponseExecutor` from `support/executors.py` when a test must exercise model-facing expression resolution, including operation inspection or notification ordering.
`StubExecutor` is reserved for tests that consume an envelope embedded in Natural text.
Asynchronous API tests await directly; the shared `execute` fixture adapts only the synchronous entry to the async test harness.

Keep a helper beside its sole consumer; move it to `support/` when several test modules need it.
Do not import another `test_*.py` module for helpers.
Use `conftest.py` for fixtures that pytest injects, and let pytest own collection and execution.
Guide snippets are executed by `tests/docs/`; reference applications by `tests/examples/`, with Harness-hosted provider acceptance in `tests/integration/harness/`.
`docs/test_links.py` builds the site and verifies internal links and anchors in rendered HTML, using MkDocs' actual Markdown and API rendering.
Runtime test fixtures live in `tests/support/`; they must not import the published examples.
The shared `package_python` fixture selects the current interpreter or verifies the fresh environment selected by `NIGHTHAWK_DISTRIBUTION_PYTHON`.
Set that variable and run `tests/docs/test_runtime_examples.py tests/distribution/` to verify both published examples and installed type contracts against the wheel.

Retain failure cases that enforce current boundaries, such as rejected effects, invalid outputs, scope isolation, and cancellation.
Remove checks whose only purpose is to remember deleted APIs, obsolete paths, exact documentation prose, or behavior already covered by the same owner.
Do not duplicate a dependency's own tests unless exercising a Nighthawk adapter contract.
Test tokenizer selection and failure propagation with a local encoding loader so those checks do not download vocabulary data.
Bound asynchronous coordination waits and propagate child-task failures, for example with `asyncio.timeout` and `asyncio.TaskGroup`; retain independent process deadlines for cleanup that may resist cancellation.

Run the deterministic suite with `uv run pytest -q`.
Backend contract tests may start local HTTP servers and subprocesses, but do not require provider credentials.
See [CONTRIBUTING.md](../CONTRIBUTING.md#integration-tests) for opt-in provider tests.
