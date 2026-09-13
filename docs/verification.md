# Verification

> Mock testing is readable after [Natural blocks](natural-blocks.md); later sections assume [Patterns](patterns.md).

A Nighthawk application has two distinct layers that need testing: the **Python logic** around Natural blocks (control flow, error handling, composition) and the **Natural blocks themselves** (whether the prompt elicits the intended LLM judgment). Each layer requires a different approach.

Mock tests cover the Python layer -- they are fast, deterministic, and free from API calls, but they bypass the LLM entirely. A mock test passes even when the Natural block text is completely wrong. Integration tests cover the Natural block layer -- they call a real LLM and verify actual judgments, but they are slower, non-deterministic, and require API credentials.

**Use both:** mock tests to lock down the deterministic Python shell, integration tests to validate that each Natural block's prompt produces correct results.

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

**Testing multi-step composition.** When a pipeline contains multiple Natural blocks, script one response per block:

```py
def test_pipeline_classify_then_summarize():
    executor = ScriptedExecutor(
        responses=[
            pass_response(category="bug"),
            pass_response(summary="Login crash on mobile"),
        ]
    )
    with nh.run(executor):
        result = triage_pipeline("App crashes when I log in on my phone")

    assert result.category == "bug"
    assert result.summary == "Login crash on mobile"
```

**Default response.** When a pipeline has many Natural blocks and only a few need specific responses, use `default_response` to avoid enumerating every step:

```py
def test_pipeline_with_default():
    executor = ScriptedExecutor(
        responses=[pass_response(category="bug")],
        default_response=pass_response(result=""),
    )
    with nh.run(executor):
        result = long_pipeline("input")

    assert result.category == "bug"
```

Scripted responses are consumed in order; once exhausted, `default_response` is returned for all subsequent Natural blocks.

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

**Gating:** integration tests are slow, costly, and non-deterministic. Gate them behind an explicit opt-in so they never run by default.

```bash
pytest -m integration
```

## When to use which

| Question | Mock test | Integration test |
|---|---|---|
| Does my Python control flow work given specific LLM outputs? | Yes | Overkill |
| Does error handling recover correctly? | Yes | Overkill |
| Are the right bindings visible to the LLM? | Yes | Also works, but slower |
| Does this Natural block actually produce useful results? | No | Yes |
| Is my prompt wording effective? | No | Yes |

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

## Tracing tool calls with OpenTelemetry

When a Natural block produces unexpected results, inspect the tool call sequence via OpenTelemetry spans. See [Observability](#observability) for setup. The `nighthawk.step` span records each tool invocation, making it possible to trace the LLM's reasoning path.

## Integration test iteration

When iterating on Natural block text, use a focused integration test with a real LLM:

```py
def test_classify_iteration():
    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna"),
    )
    with nh.run(step_executor):
        result = classify("ambiguous input that failed before")

    assert result in ("positive", "negative", "neutral")
```

Run repeatedly with `pytest -x -k test_classify_iteration` to validate prompt changes against specific inputs that previously failed.

## Contract checks and model evaluations

Verify deterministic runtime mechanics separately from model judgment.
Scripted executors use `StepRequest` and return resolved `StepCandidate` values; the common runtime still validates outputs, returns, and allowed outcomes.
Exercise live lexical lookup, unbound names, annotation acquisition, joined child snapshots, assignment validation and partial effects, ordered operations, awaited approval, rewrites, and terminal delivery through their actual runtime boundaries.
The [acceptance cases](specification.md#145-behavioral-acceptance-cases) define review scenarios, and the [implementation audit](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/evals/model_tools/evidence/20260911-ideal-state-audit.md) maps them to existing tests and dated results.

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

The [inspection assessment](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/evals/model_tools/evidence/20260911-inspection-assessment.md) records 804 model trials and the selection of Python inspection helpers on the existing evaluation/assignment surface.
The [protocol](https://github.com/kurusugawa-computer/nighthawk-python/blob/main/evals/model_tools/evidence/20260911-inspection-study-protocol.md) owns experimental controls and source snapshots.
This is a bounded selection with reported failures, not statistical equivalence or a universal claim about models or backends.

## Observability

Nighthawk emits [OpenTelemetry](https://opentelemetry.io/) spans for runs, scopes, and step executions. If your application has an OpenTelemetry tracer configured, Nighthawk traces appear automatically -- no Nighthawk-specific setup is required.

### Span hierarchy

Each Nighthawk execution produces a tree of spans:

| Span | Created by | Identity attribute |
|---|---|---|
| `nighthawk.run` | `nh.run()` context manager | `run.id` |
| `nighthawk.scope` | `nh.scope()` context manager | `scope.id` |
| `nighthawk.step` | Each Natural block execution | `step.execution.id` (invocation), `step.source_location` (`python_module:line`) |

### Step events

Events are emitted on the `nighthawk.step` span:

| Event | When | Key attributes |
|---|---|---|
| `nighthawk.step.completed` | Natural block succeeds | `outcome_kind` |
| `nighthawk.step.raised` | `raise` outcome (domain-level) | `outcome_kind`, `raise_message`, `raise_error_type` |
| `nighthawk.step.failed` | Internal Nighthawk failure | `error_kind`, `error_message` |

The `raise` outcome is domain-level behavior (the LLM chose to signal an error). Internal failures (`failed`) indicate a Nighthawk-side problem (invalid JSON, validation failure, etc.).

### Resilience events

The `budget` transformer emits an event on the current span when a budget limit is breached:

| Event | When | Key attributes |
|---|---|---|
| `nighthawk.budget.exceeded` | Token or cost budget exceeded | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |

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

See [Specification Section 10.1](specification.md#101-observability-contract-opentelemetry-spanevent) for the full span and event specification.

For authoritative execution history, install [terminal delivery](runtime-configuration.md#record-completed-executions). StepCompleted is delivered after caller assignments; StepFailed includes return-await failures and their stage. Tracing is optional and independent of ledger recording. A delivery failure can add `nighthawk.step.delivery_failed`, but never a second execution terminal event. Cancellation is recorded separately as `nighthawk.step.interrupted`.

## Embedded invocation and completion verification

Run the deterministic example with `uv run python examples/embedded_execution.py`.
It needs no provider credentials and asserts synchronous/asynchronous invocation, refusal before effects, model restoration, request accounting, and notification cleanup completion.
Expected output is:

```text
refused_body_effects=0
models=A,B,A
observed_tokens=120
next_request_refused=True
returned_after_notification_cleanup=True
```

`tests/natural/test_function_definition.py` verifies exact provenance and collection of abandoned namespaces.
`tests/natural/test_function_call.py` verifies context timing, parent identities, guarded continuations, and synchronous coroutine-return identity.
`tests/runtime/test_async_bridge.py` exercises interruption races in independently watched subprocesses.
It also verifies that caller interruption preserves a root task's existing cancellation cleanup in direct and worker execution, including inspectable cleanup failures.
Lifecycle regressions release resistant cleanup explicitly and require it to finish before return.
`tests/runtime/test_model_selection.py` and `test_request_observation.py` cover preserving resolution, approximate admission, failure, cancellation, and concurrent overrun.
Expression, assignment, and tool ownership tests distinguish Core-created await wrappers from retained application coroutines.

For distribution validation, build a wheel, install it in a fresh environment outside the source tree, copy the example there, and run the environment's Python with `-I` and no `PYTHONPATH`.
Verify `nighthawk.__file__` points to that environment's installed package.
A source-tree test alone is not installed-distribution evidence.
Real-provider tests remain opt-in supplementary evidence; they cannot establish race or cleanup guarantees.
