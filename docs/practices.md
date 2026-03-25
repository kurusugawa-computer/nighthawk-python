# Nighthawk Practices

This guide covers observability, writing guidelines, binding function design, and testing. It assumes you have completed the [Tutorial](tutorial.md).

## 1. Observability

Nighthawk emits [OpenTelemetry](https://opentelemetry.io/) spans for runs, scopes, and step executions. If your application has an OpenTelemetry tracer configured, Nighthawk traces appear automatically — no Nighthawk-specific setup is required.

### Span hierarchy

Each Nighthawk execution produces a tree of spans:

| Span | Created by | Identity attribute |
|---|---|---|
| `nighthawk.run` | `nh.run()` context manager | `run.id` |
| `nighthawk.scope` | `nh.scope()` context manager | `scope.id` |
| `nighthawk.step` | Each Natural block execution | `step.id` (format: `python_module:line`) |
| `nighthawk.step_executor` | The step executor's LLM call | — |

### Step events

Events are emitted on the `nighthawk.step` span:

| Event | When | Key attributes |
|---|---|---|
| `nighthawk.step.completed` | Natural block succeeds | `outcome_kind` |
| `nighthawk.step.raised` | `raise` outcome (domain-level) | `outcome_kind`, `raise_message`, `raise_error_type` |
| `nighthawk.step.failed` | Internal Nighthawk failure | `error_kind`, `error_message` |

The `raise` outcome is domain-level behavior (the LLM chose to signal an error). Internal failures (`failed`) indicate a Nighthawk-side problem (invalid JSON, validation failure, etc.).

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

See [design.md Section 10.1](design.md#101-observability-contract-opentelemetry-spanevent) for the full span and event specification.

## 2. Writing Guidelines

### Responsibility split

**Use Natural when the task requires judgment** — decisions that depend on interpretation, world knowledge, or subjective evaluation:

- Classification and routing (e.g., categorize a support ticket).
- Text generation (e.g., summarize, draft, translate, reformulate).
- Interpretation of ambiguous or unstructured input.
- Selection among options based on context (e.g., choose the best policy).

**Use Python for everything deterministic** — operations whose result is fully determined by the input:

- Computation (arithmetic, string manipulation, data transformation).
- Control flow (loops, conditionals, sequencing of Natural blocks).
- I/O and side effects (file operations, API calls, database queries).
- Validation, type enforcement, and error recovery.
- State management and data flow between Natural blocks.

**Decision rule:** if the correct output can be computed without an LLM, use Python. Natural blocks add latency, cost, and non-determinism — reserve them for tasks that genuinely require LLM capabilities.

A corollary: do not attempt to "compile" a Natural block into equivalent Python code via a one-time LLM translation. Natural blocks exist for tasks whose correct output depends on interpretation, world knowledge, and context that cannot be captured in static code. If the task could be reduced to deterministic Python, it should be written in Python from the start. See [Philosophy](philosophy.md#why-evaluate-every-time) for the full rationale.

### Type boundary placement

The responsibility split above determines *what* goes into a Natural block. A related question is *where* the typed input boundary sits.

For deterministic functions (no Natural blocks), the boundary is at the function entry point — use typed inputs:

```py
from pydantic import BaseModel

class ScoreInput(BaseModel):
    base: int
    bonus: int
    multiplier: float = 1.0

def compute_score(score_input: ScoreInput) -> int:
    return int((score_input.base + score_input.bonus) * score_input.multiplier)
```

For judgment-heavy functions (containing Natural blocks), the boundary moves *inside* the function. Accept flexible inputs at the entry point and let the Natural block interpret them into typed intermediates:

`JsonableValue` is a type alias for JSON-serializable Python values (`dict | list | str | int | float | bool | None`). See [design.md Section 5.3](design.md#53-supporting-types) for the full definition.

```py
from pydantic import BaseModel
from nighthawk import JsonableValue

class ReviewVerdict(BaseModel):
    approved: bool
    reason: str
    risk_level: str

@nh.natural_function
def judge_review(review_data: str | JsonableValue) -> ReviewVerdict:
    verdict: ReviewVerdict
    """natural
    Analyze <review_data> and produce a structured <:verdict>.
    """
    return verdict
```

Here, `review_data` accepts flexible input because the Natural block handles interpretation. The type boundary is at `<:verdict>` — the write binding where the LLM commits a typed `ReviewVerdict`.

When designing function contracts, document where the type boundary lies. Do not assume it is always at the function signature.

### Rules

1. Write one integrated instruction body per block; do not split into a "bindings list" then "instructions".
2. One task per block. A task may range from a lightweight classification to an autonomous multi-step operation (with [coding agent backends](coding-agent-backends.md)). The key constraint is a clear contract: one set of input bindings, one set of output bindings, one outcome. If a block makes two independent decisions, split it into two blocks connected by Python.
3. Cross-block data flow must be explicit. Use Python locals, the carry pattern, or f-string injection.
4. Write bindings (`<:name>`) may be pre-declared or not. Type annotations help agent behavior and host-side validation/coercion.
5. Mutable context objects use `<name>` (read binding), not `<:name>` (write binding).
6. Keep function parameters and locals minimal — only bind invocation-specific data. Reference module-level names via `<name>` read bindings so they appear in GLOBALS with full type information ([Tutorial Section 3](tutorial.md#keep-locals-minimal)).
7. Prefer binding functions (local or module-level) for all callable needs. Reserve `@nh.tool` for cases that require `RunContext[StepContext]` access.
8. Full coverage requirements are enforced by Python loops.
9. Error behavior is explicit at the correct boundary.

## 3. Designing Binding Functions

Rules 6 and 7 say to keep locals minimal and prefer binding functions. This section explains *how* to design those binding functions.

Each parameter in a binding function signature is a decision point the LLM must evaluate. Fewer parameters mean lower cognitive load and more reliable tool use.

**Principle:** justify each parameter against LLM cognitive load. Simple writes (e.g., setting a value at creation) are acceptable. Complex reads (e.g., multi-predicate queries) are not — compose those in Python.

Wrong — too many parameters force the LLM to construct a complex query:

```py
def find_items(
    category: str,
    min_score: float,
    max_score: float,
    tags: list[str],
    created_after: str,
    sort_by: str,
) -> list[dict]:
    """Find items matching all filter criteria."""
    ...
```

Correct — compose the complex query in Python, expose a simple binding function:

```py
def find_top_items(category: str) -> list[dict]:
    """Return the highest-scored recent items in a category."""
    return query_items(
        category=category,
        min_score=0.8,
        tags=get_relevant_tags(category),
        created_after=recent_cutoff(),
        sort_by="score_desc",
    )
```

The LLM sees a one-parameter function with a clear intent. The filtering logic lives in Python where it can be tested and debugged.

This principle extends to project architecture: compose domain-specific helper functions in Python and expose them to Natural blocks as binding functions.

```py
# Python API — full flexibility, tested independently
def get_feedback_summary(topic: str, max_items: int = 10) -> str:
    items = fetch_feedback(topic=topic, limit=max_items)
    return format_summary(items)

# Natural block sees only what it needs
@nh.natural_function
def analyze_feedback(topic: str) -> str:
    result = ""
    """natural
    Call <get_feedback_summary> for <topic> and set <:result>
    to an actionable recommendation.
    """
    return result
```

## 4. Testing Natural Functions

A Nighthawk application has two distinct layers that need testing: the **Python logic** around Natural blocks (control flow, error handling, composition) and the **Natural blocks themselves** (whether the prompt elicits the intended LLM judgment). Each layer requires a different approach.

Mock tests cover the Python layer — they are fast, deterministic, and free from API calls, but they bypass the LLM entirely. A mock test passes even when the Natural block text is completely wrong. Integration tests cover the Natural block layer — they call a real LLM and verify actual judgments, but they are slower, non-deterministic, and require API credentials.

**Use both:** mock tests to lock down the deterministic Python shell, integration tests to validate that each Natural block's prompt produces correct results.

### Mock tests

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
    executor = ScriptedExecutor(responses=[
        pass_response(label="positive"),
    ])
    with nh.run(executor):
        result = classify("Great product!")

    assert result == "positive"
```

`ScriptedExecutor` does not call an LLM. You script what it returns with **outcome factories**:

| Factory | Outcome | Use case |
|---|---|---|
| `pass_response(**bindings)` | pass | Normal completion with binding values |
| `raise_response(message, *, error_type=None)` | raise | Test error handling paths |
| `return_response(reference_path, **bindings)` | return | Early return from Natural function |
| `break_response()` | break | Exit enclosing loop |
| `continue_response()` | continue | Skip to next iteration |

#### Testing error handling

Use `raise_response` to verify that your code handles LLM failures gracefully:

```py
from nighthawk.testing import raise_response

def test_fallback_on_error():
    executor = ScriptedExecutor(responses=[
        raise_response("cannot interpret input", error_type="ValueError"),
    ])
    with nh.run(executor):
        try:
            result = classify("???")
        except ValueError:
            result = "unknown"

    assert result == "unknown"
```

#### Testing multi-step composition

When a pipeline contains multiple Natural blocks, script one response per block:

```py
def test_pipeline_classify_then_summarize():
    executor = ScriptedExecutor(responses=[
        pass_response(category="bug"),
        pass_response(summary="Login crash on mobile"),
    ])
    with nh.run(executor):
        result = triage_pipeline("App crashes when I log in on my phone")

    assert result.category == "bug"
    assert result.summary == "Login crash on mobile"
```

#### Default response

When a pipeline has many Natural blocks and only a few need specific responses, use `default_response` to avoid enumerating every step:

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

#### Verifying binding wiring

Use recorded calls to check that the right data is visible to the LLM:

```py
def test_helper_is_discoverable():
    executor = ScriptedExecutor(responses=[pass_response(result="")])
    with nh.run(executor):
        analyze(query="test")

    call = executor.calls[0]
    assert "helper" in call.step_globals   # binding function visible in GLOBALS
    assert "query" in call.step_locals     # parameter visible in LOCALS
    assert "result" in call.binding_names  # write binding registered
```

#### Callback executor

When the mock response depends on the input, use `CallbackExecutor`:

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

### Integration tests

Integration tests call a real LLM and validate the judgment. This is where you verify that the Natural block text actually works.

```py
import nighthawk as nh


def test_classify_with_real_llm():
    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-mini"),
    )
    with nh.run(step_executor):
        result = classify("Great product, highly recommend!")

    assert result in ("positive", "negative", "neutral")
```

**Assertion strategy:** assert on type, value range, and semantic consistency rather than exact string matches. LLMs are non-deterministic; brittle equality checks produce flaky tests.

Gate integration tests behind an environment variable so they do not run in every CI job:

```py
import os
import pytest

if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
    pytest.skip("Integration tests disabled", allow_module_level=True)
```

### When to use which

| Question | Mock test | Integration test |
|---|---|---|
| Does my Python control flow work given specific LLM outputs? | Yes | Overkill |
| Does error handling recover correctly? | Yes | Overkill |
| Are the right bindings visible to the LLM? | Yes | Also works, but slower |
| Does this Natural block actually produce useful results? | **No** | Yes |
| Is my prompt wording effective? | **No** | Yes |

## 5. Common Mistakes

| Mistake | Why it breaks | Fix |
|---|---|---|
| Pass a callable as a parameter with generic type (`object`, `Any`) | Signature erased in LOCALS; LLM cannot discover arguments | Reference via `<name>` read binding so it appears in GLOBALS with full signature ([Tutorial Section 3](tutorial.md#keep-locals-minimal)) |
| Use `<:carry>` (write binding) for mutable context | Rebinding breaks the caller's reference | Use `<carry>` (read binding); mutate in-place ([Tutorial Section 5](tutorial.md#the-carry-pattern)) |
| Put two independent tasks in one block | Non-deterministic, hard to test, unclear contract | Split into two blocks connected by Python |
| Use Natural for deterministic computation | Wastes latency/cost, adds non-determinism | Use Python ([Section 2](#2-writing-guidelines)) |
| Forget type annotations on write bindings | No validation or coercion at commit time | Always annotate `<:name>` bindings |
| Duplicate module-level constants as function parameters | Moves stable values from GLOBALS to LOCALS, wastes tokens | Reference via `<name>` read binding ([Tutorial Section 3](tutorial.md#keep-locals-minimal)) |
| Try to "compile" a Natural block into deterministic Python | Judgment tasks cannot be reduced to static code; input space is unbounded | Keep the Natural block; use Python only for deterministic operations ([Philosophy](philosophy.md#why-evaluate-every-time)) |

## 6. Debugging Natural Blocks

### Inspecting the assembled prompt

Enable `DEBUG` logging on the `nighthawk` logger to see the full prompt sent to the LLM:

```py
import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("nighthawk").setLevel(logging.DEBUG)
```

The log output includes the rendered PROGRAM, LOCALS, and GLOBALS sections, making it easy to verify that bindings and context appear as expected.

### Diagnosing `<snipped>` markers

When the LOCALS or GLOBALS section is too large, Nighthawk truncates it and appends a `<snipped>` marker. A diagnostic log message is emitted on the `nighthawk` logger. To fix:

- Increase `locals_max_tokens` or `globals_max_tokens` in `StepContextLimits`.
- Reduce the number of locals by moving stable values to module-level (GLOBALS).
- Use f-string injection for pre-formatted context instead of large binding values.

### Tracing tool calls with OpenTelemetry

When a Natural block produces unexpected results, inspect the tool call sequence via OpenTelemetry spans. See [Section 1](#1-observability) for setup. The `nighthawk.step` span records each tool invocation, making it possible to trace the LLM's reasoning path.

### Integration test iteration

When iterating on Natural block text, use a focused integration test with a real LLM:

```py
def test_classify_iteration():
    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-mini"),
    )
    with nh.run(step_executor):
        result = classify("ambiguous input that failed before")

    assert result in ("positive", "negative", "neutral")
```

Run repeatedly with `pytest -x -k test_classify_iteration` to validate prompt changes against specific inputs that previously failed. Gate behind `NIGHTHAWK_RUN_INTEGRATION_TESTS=1` for CI.
