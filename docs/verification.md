# Verification

> Mock testing is readable after [Natural blocks](natural-blocks.md); later sections assume [Patterns](patterns.md).

Test Python control flow and validation with deterministic executors; test Natural judgment with opt-in real-model calls.
A mock verifies the surrounding Python behavior, not the quality of the prompt.

## Mock tests

The `nighthawk.testing` module provides `ScriptedExecutor`, which returns scripted responses and records every Natural block invocation. Use it to test the Python logic that surrounds Natural blocks.

```py
import nighthawk as nh
from nighthawk.testing import ScriptedExecutor, pass_response


@nh.natural_function
def classify(text: str) -> str:
    label: str = ""
    """natural
    Read <text> and set <:label> to one of: positive, negative, neutral.
    """
    return label


def test_classify_returns_scripted_label():
    executor = ScriptedExecutor(
        responses=[
            pass_response(label="positive"),
        ]
    )
    with nh.run(executor):
        result = classify("Great product!")

    assert result == "positive"
```

`ScriptedExecutor` does not call an LLM. You script what it returns with **outcome factories**:

| Factory | Outcome | Use case |
|---|---|---|
| `pass_response(**bindings)` | pass | Normal completion with binding values |
| `raise_response(message, *, error_type=None)` | raise | Test error handling paths |
| `return_response(expression, **bindings)` | return | Early return from Natural function |
| `break_response()` | break | Exit enclosing loop |
| `continue_response()` | continue | Skip to next iteration |

**Testing error handling.** Use `raise_response` to verify that your code handles LLM failures gracefully:

```py
from nighthawk.testing import raise_response


def test_fallback_on_error():
    executor = ScriptedExecutor(
        responses=[
            raise_response("cannot interpret input", error_type="ValueError"),
        ]
    )
    with nh.run(executor):
        try:
            result = classify("???")
        except ValueError:
            result = "unknown"

    assert result == "unknown"
```

For a pipeline, supply one response per block in execution order.
`default_response` supplies the response used after the explicit sequence is exhausted.

**Verifying binding wiring.** Use recorded calls to check that the right data is visible to the LLM:

```py
def test_helper_is_discoverable():
    executor = ScriptedExecutor(responses=[pass_response(result="")])
    with nh.run(executor):
        analyze(query="test")

    call = executor.calls[0]
    assert "helper" in call.step_globals  # binding function visible in GLOBALS
    assert "query" in call.step_locals  # parameter visible in LOCALS
    assert "result" in call.binding_names  # write binding registered
```

**Callback executor.** When the mock response depends on the input, use `CallbackExecutor`:

```py
from nighthawk.testing import CallbackExecutor, StepCall, StepResponse


def handler(call: StepCall) -> StepResponse:
    text = call.step_locals.get("text", "")
    if isinstance(text, str) and "urgent" in text:
        return pass_response(priority="high")
    return pass_response(priority="normal")


def test_urgent_routing():
    executor = CallbackExecutor(handler)
    with nh.run(executor):
        assert triage("urgent outage") == "high"
        assert triage("minor typo") == "normal"
```

`CallbackExecutor` records every call in `executor.calls`, just like `ScriptedExecutor`. Use it to verify binding wiring alongside dynamic response logic.

## Integration tests

Integration tests call a real LLM and validate the judgment. This is where you verify that the Natural block text actually works.

```py
import pytest
import nighthawk as nh


@pytest.mark.integration
def test_classify_with_real_llm():
    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna"),
    )
    with nh.run(step_executor):
        result = classify("Great product, highly recommend!")

    assert result in ("positive", "negative", "neutral")
```

**Assertion strategy:** assert on type, value range, and semantic consistency rather than exact string matches. LLMs are non-deterministic; brittle equality checks produce flaky tests.

**Gating:** configure the test suite to exclude integration tests by default, then opt in with a marker selection:

```bash
pytest -m integration
```

Keep representative failures as regression inputs and use `pytest -x -k test_name` for focused prompt iteration.

## Inspecting the assembled prompt

Enable `DEBUG` logging on the `nighthawk` logger to see the full prompt sent to the LLM:

```py
import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("nighthawk").setLevel(logging.DEBUG)
```

The log output includes the rendered PROGRAM, LOCALS, and GLOBALS sections, making it easy to verify that bindings and context appear as expected.

## Diagnosing `<snipped>` markers

When the LOCALS or GLOBALS section is too large, Nighthawk truncates it and appends a `<snipped>` marker. The underlying data remains in Python memory and is still accessible through binding functions at runtime. A diagnostic log message is emitted on the `nighthawk` logger. To improve prompt coherence:

- Increase `locals_max_tokens` or `globals_max_tokens` in `StepContextLimits`.
- Reduce the number of locals by moving stable values to module-level (GLOBALS).
- Use f-string injection for pre-formatted context instead of large binding values.

## Contract checks and model evaluations

Verify deterministic runtime mechanics separately from model judgment.
Scripted executors use `StepRequest` and return resolved `StepCandidate` values; the common runtime validates outputs, returns, and allowed outcomes.
Exercise live lexical lookup, unbound names, annotation acquisition, joined child snapshots, assignment validation and partial effects, ordered operations, awaited approval, rewrites, and terminal delivery through their actual runtime boundaries.
The [acceptance cases](specification.md#145-behavioral-acceptance-cases) define runtime review scenarios.

Real model tests should check observable task results, child calls, selected capabilities, and retrieval of omitted context.
Type validation alone cannot prove a judgment's factual accuracy, call count, or effect correctness.
The [composition integration tests](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/tests/integration/test_natural_composition.py) exercise Python iteration, judgment-selected children inheriting review policy, and the ten-call counter example with actual Natural functions.
Their inputs are synthetic; inspect assertions and lifecycle facts rather than requiring a particular expression sequence.

### Interface evaluation criteria

Semantic success, permitted control results, and correct effects take precedence over call count, token usage, and latency.
Keep failed and duplicate effects in the results; efficiency cannot compensate for lost semantic reliability.
Use independent repetitions, varied formulations, and representative models/backends; report sample sizes, uncertainty, and unavoidable differences in guidance.
When evidence does not distinguish acceptable alternatives, prefer lower implementation, explanation, and maintenance burden across the runtime, adapters, and guidance.
Fewer tools alone do not establish a simpler or more reliable system.

## Observability

Nighthawk emits [OpenTelemetry](https://opentelemetry.io/) spans for runs, scopes, and step executions. If your application has an OpenTelemetry tracer configured, Nighthawk traces appear automatically -- no Nighthawk-specific setup is required.

### Span hierarchy

Each Nighthawk execution produces a tree of spans:

| Span | Created by | Identity attribute |
|---|---|---|
| `nighthawk.run` | `nh.run()` context manager | `run.id` |
| `nighthawk.scope` | `nh.scope()` context manager | `scope.id` |
| `nighthawk.step` | Each Natural block execution | `step.execution.id` (invocation), `step.source_location` (`python_module:line`) |

Inspect step spans to correlate tool calls, outcomes, interruptions, and delivery failures.
[The observability contract](specification.md#101-observability-contract-opentelemetry-spanevent) defines event names and attributes.

### Local trace inspection with otel-tui

Start an [otel-tui](https://github.com/ymtdzzz/otel-tui) collector:

```bash
docker run --rm -it -p 4318:4318 --name otel-tui ymtdzzz/otel-tui:latest
```

Then run with the collector endpoint:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 python my_script.py
```

Traces appear in the terminal UI in real time.

Use [lifecycle recording](runtime-configuration.md#record-completed-executions) when the application needs authoritative block records; traces support diagnosis.

## Embedded invocation and completion verification

The runtime configuration guide contains complete, independently executable examples for:

- [Whole-function admission](runtime-configuration.md#whole-function-admission): refusal before body effects or model resolution, followed by accepted synchronous and asynchronous calls.
- [Preserving host model policy](runtime-configuration.md#preserve-host-model-policy): model selection retains its wrapper and restores the parent executor.
- [Request usage and admission](runtime-configuration.md#request-usage-and-admission): a host accumulator refuses a request within an unfinished block, independently of aggregate metering.
- [Completion and interruption](runtime-configuration.md#completion-and-interruption): a cancelled function waits for owned notification cleanup.

These examples use scripted or simulated model responses and need no credentials.
`tests/docs/test_runtime_examples.py` extracts the marked code blocks directly from the guide, writes real Python files for Natural source inspection, and executes each in a subprocess with a 30-second timeout.
Assertions in the published code check the contract; the tests also verify the reported result.
Run them with:

```sh
uv run pytest -q tests/docs/test_runtime_examples.py
```

Test cancellation with a controlled cleanup release and an independent process timeout, so regressions cannot hang the suite.
The runtime and lifecycle suites cover additional failure and concurrency cases using test-owned fixtures in `tests/support/`.
Real-provider tests remain opt-in supplementary evidence; they cannot establish race or cleanup guarantees.

### Validate an installed distribution

Use the same extracted examples to check a wheel in a fresh environment outside the source tree.
From the repository root, run:

```sh
validation_directory=$(mktemp -d)
uv build --wheel --out-dir "$validation_directory/dist"
uv venv "$validation_directory/environment"
uv pip install --python "$validation_directory/environment/bin/python" "$validation_directory"/dist/*.whl
NIGHTHAWK_DISTRIBUTION_PYTHON="$validation_directory/environment/bin/python" \
    uv run pytest -q tests/docs/test_runtime_examples.py
```

The test runner extracts code from the documentation, while the fresh environment executes it with `python -I` from a temporary directory.
It verifies that `nighthawk.__file__` belongs to that environment rather than an editable source checkout.
The child processes need only the wheel and its declared dependencies; they do not import the repository's test helpers.
CI runs this check after building the distribution.
A source-tree test alone is not installed-distribution evidence.
