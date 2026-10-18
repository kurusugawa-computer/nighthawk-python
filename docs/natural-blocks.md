# Natural blocks
<!-- prompt-example markers (e.g. prompt-example:basic-binding) are test anchors used by tests/docs/test_prompt_examples.py to verify that documented prompt examples match actual rendering output. -->

> This page assumes you have completed [Quickstart](quickstart.md).

This page covers what Natural blocks are and how to design them, from prompt structure through binding functions and structured output.

## Running the examples

All examples assume the [Quickstart](quickstart.md) setup:

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna"),
)

with nh.run(step_executor):
    ...  # Call natural functions here
```

See [Executors](executors.md) for model identifiers and backend options.

## Anatomy of a Natural block

Every Natural block execution follows the same pattern: Nighthawk assembles a prompt, sends it to the LLM, and the LLM responds with tool calls and a final outcome. Understanding the prompt structure is the key to writing effective Natural blocks.

### What the LLM receives

When you write:

```py
@nh.natural_function
def classify_priority(text: str) -> str:
    priority: str = "normal"
    """natural
    Read <text> and update <:priority> with one of: low, normal, high.
    """
    return priority
```

And call `classify_priority("Server is on fire!")`, Nighthawk assembles this user prompt:

<!-- prompt-example:basic-binding -->
```py
Runtime inspection: __nighthawk_runtime_0.inspect(value).
Name discovery: __nighthawk_runtime_0.environment.discover(); named contracts: __nighthawk_runtime_0.environment.describe('name').
Application names, including inspect, retain their Python meaning. These runtime references cannot be assignment roots.
Displayed annotations are best-effort metadata and may be unresolved source spelling; they are not evaluated for presentation.

<<<NH:PROGRAM>>>
Read <text> and update <:priority> with one of: low, normal, high.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
Origin: python; name: priority; output contract: str
priority: str = "normal"
Origin: python; name: text
text: str = "Server is on fire!"
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>

<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:basic-binding -->

Three sections:

- **`<<<NH:PROGRAM>>>`** -- your Natural block text (after sentinel removal and `textwrap.dedent`).
- **`<<<NH:LOCALS>>>`** -- bounded working context, with origins and output contracts.
  Explicit references and outputs take priority, followed by Python working locals and inherited state, with alphabetical ordering within each priority class.
- **`<<<NH:GLOBALS>>>`** -- referenced globals and builtins, plus scoped task capabilities not already presented as working state.

Design the locals at the block boundary to show what matters for the task.
Budgets can omit even explicit references; omitted names remain available through environment discovery.
Presentation is a view of accessible state, not its authority or a replacement for live objects.

### One block, one task

Each Natural block performs one task with declared output bindings and one outcome.
There is no implicit model message history between blocks.
Python lexical values and explicitly scoped task references supply context to each fresh interaction (see [Patterns: cross-block composition](patterns.md#cross-block-composition)).

## Providing data to a block

Pass external input as Python values through **bindings**.
Use **f-string injection** only for trusted program fragments; interpolation changes Natural source before execution.

### Read bindings (`<name>`)

A read binding resolves a required value and prioritizes it in the prompt.
It does not restrict access to other lexical, inherited, or scoped task values.
An existing Python name can be rebound only if also declared as an output.

```py
@nh.natural_function
def greet(user_name: str, language: str) -> str:
    greeting = ""
    """natural
    Compose a short greeting for <user_name> in <language> and set <:greeting>.
    """
    return greeting
```

`user_name` and `language` are read bindings -- the LLM can read their values, but it cannot rebind the names. If a read binding holds a mutable object (e.g., a `list`), the LLM can mutate it in-place (see [Patterns: the carry pattern](patterns.md#the-carry-pattern)).

### Write bindings (`<:name>`)

A write binding allows the LLM to set a new value. The value is committed back into Python locals after the block.

Pre-declared (with type annotation and initial value):

```py
@nh.natural_function
def extract_sentiment(review: str) -> str:
    sentiment: str = "neutral"
    """natural
    Read <review> and update <:sentiment> with one of: positive, neutral, negative.
    """
    return sentiment
```

Annotation only (type without initial value):

```py
@nh.natural_function
def extract_topic(article: str) -> str:
    topic: str
    """natural
    Read <article> and set <:topic> to the main topic.
    """
    return topic
```

Not pre-declared:

```py
@nh.natural_function
def detect_language(text: str):
    """natural
    Read <text> and set <:language> to the detected language code.
    """
    # `language` is intentionally introduced by <:language>.
    return language
```

An explicit output annotation determines its validation contract at block entry.
Without one, the runtime infers the initialized value's type, except for `None` and a plain `object()`; otherwise the output is unconstrained.
Annotate collections explicitly when element types matter: `findings: list[Finding] = []` retains its element contract, while `findings = []` selects only `list`.
An unresolved explicit annotation fails preparation instead of silently falling back to inference.
The standard executor selects all bound declared outputs for final validation even if the model never assigns them; an initial Raise discards pending outputs.
Custom executors may supply an explicit complete output selection; see [output selection](executors.md#output-selection).
Output contracts constrain values, not the factual correctness of a judgment.
See [binding contracts](specification.md#7-natural-dsl-bindings) for annotation acquisition and global/nonlocal writeback.

### Pydantic model write bindings

Write bindings can use Pydantic models for structured output with automatic validation:

```py
from pydantic import BaseModel


class ReviewVerdict(BaseModel):
    approved: bool
    reason: str
    risk_level: str


@nh.natural_function
def judge_review(review_data: str) -> ReviewVerdict:
    verdict: ReviewVerdict
    """natural
    Analyze <review_data> and produce a structured <:verdict>.
    Set approved, reason, and risk_level fields.
    """
    return verdict
```

When the LLM assigns a value to `verdict`, Nighthawk validates and coerces it to a `ReviewVerdict` instance.
Assignment validation failures give the model recoverable feedback; boundary validation failure prevents pending Python name assignments.
For direct model-field mutation, use ordinary declared Pydantic fields with `validate_assignment=True`.
Their constraints, validators, and frozen restrictions apply through Pydantic's assignment machinery.
Use ordinary Python helpers for properties, custom objects, and other mutation routes; helper effects are not automatically validated or rolled back.

**How write bindings appear in the prompt.** Initialized outputs appear with their values and selected contracts. Unbound outputs have no value preview; their declarations and contracts remain discoverable through the environment. If a context budget omits an entry, the presentation identifies the omission and gives retrieval references.

### Multimodal bindings

A binding may hold a Pydantic AI multimodal value -- `BinaryContent`, `ImageUrl`, `AudioUrl`, `DocumentUrl`, `VideoUrl`, or `UploadedFile`. These are first-class binding values: reference them with `<name>` like any other binding, and Nighthawk renders the inline placeholder (`<image>` for image media, `<file>` otherwise) in the LOCALS or GLOBALS line. Explicit dotted leaf references use the same mechanism, and dotted `list` / `tuple` leaves are hoisted when they satisfy the same `UserContent` rule as top-level bindings. `UploadedFile` is only usable as native user-prompt content on provider-backed executors that can resolve the provider-owned file reference; coding-agent backends reject it at the user-prompt boundary.

Transport depends on the executor:

- Provider-backed executors that accept Pydantic AI `UserContent` send the value natively to the VLM API.
- Coding-agent backends text-project the value (staged local files or URL references). See [Coding agent backends: multimodal inputs](coding-agent-backends.md#multimodal-inputs).

Nested multimodal values inside dicts, models, and objects are not recursively hoisted. Keep the prompt boundary explicit by lifting the media-bearing leaf into a helper variable before the Natural block:

```py
def summarize_report(report: Report) -> str:
    report_scan = report.attachments.scan_image
    summary = ""
    """natural
    Read <report_scan> and summarize the visual evidence in <:summary>.
    """
    return summary
```

This keeps the media selection visible in ordinary Python, avoids accidental traversal of large object graphs, and lets normal LOCALS budgeting decide whether the helper variable is rendered.

For the normative rendering and transport rules, including dotted-reference hoisting and nested multimodal behavior, see [Specification Section 8.2](specification.md#822-locals-summary).

### f-string injection

Inline f-string blocks embed Python expressions directly into the Natural program text. The expression is evaluated when Python evaluates the f-string -- before the LLM sees the prompt.

```py
PROJECT_POLICY = ["safety-first", "concise-output", "cite-assumptions"]


@nh.natural_function
def choose_policy(post: str) -> str:
    selected_policy = ""
    f"""natural
    Read <post>.
    Available policies: {PROJECT_POLICY}
    Select the single best policy and set <:selected_policy>.
    """
    return selected_policy
```

Calling `choose_policy("Breaking: earthquake hits downtown")` produces:

<!-- prompt-example:fstring-injection -->
```py
Runtime inspection: __nighthawk_runtime_0.inspect(value).
Name discovery: __nighthawk_runtime_0.environment.discover(); named contracts: __nighthawk_runtime_0.environment.describe('name').
Application names, including inspect, retain their Python meaning. These runtime references cannot be assignment roots.
Displayed annotations are best-effort metadata and may be unresolved source spelling; they are not evaluated for presentation.

<<<NH:PROGRAM>>>
Read <post>.
Available policies: ['safety-first', 'concise-output', 'cite-assumptions']
Select the single best policy and set <:selected_policy>.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
Origin: python; name: post
post: str = "Breaking: earthquake hits downtown"
Origin: python; name: selected_policy; output contract: str
selected_policy: str = ""
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>

<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:fstring-injection -->

Notice: `{PROJECT_POLICY}` was evaluated into literal text in the PROGRAM section, while `post` and `selected_policy` appear as bindings in LOCALS.

Member accesses and function results work too:

```py
from pydantic import BaseModel


class Config(BaseModel):
    max_length: int = 100
    style: str = "formal"


@nh.natural_function
def generate(config: Config, topic: str) -> str:
    output = ""
    f"""natural
    Write about <topic> in {config.style} style.
    Keep the output under {config.max_length} words.
    Set <:output>.
    """
    return output
```

**Note:** To use literal angle brackets in program text without creating a binding, escape with a backslash: `\<name>` renders as `<name>` in the prompt without binding resolution. See [Specification Section 8.2.3](specification.md#823-globals-summary) for details.

### Choosing between bindings and injection

| | f-string injection | `<name>` binding |
|---|---|---|
| Evaluation time | When Python evaluates the f-string literal | At LLM prompt construction |
| Appears in | Natural program text directly | Locals summary |
| Token control | Full -- you decide the exact text | Governed by `context_limits` ([Runtime configuration](runtime-configuration.md#context-limits)) |
| LLM can mutate | No (text is baked in) | In-place only (e.g., `list.append()`) |
| Brace escaping | `{{` / `}}` to produce literal `{` / `}` | N/A |
| Best for | Static config, pre-formatted context, computed values | Mutable state, objects the LLM needs to inspect or modify |

## Functions and discoverability

The LLM discovers callable functions from the LOCALS and GLOBALS sections of the prompt. Callable values are rendered as their signature, with the first line of the docstring appended as `# `.

### Local functions

```py
@nh.natural_function
def compute_score_with_local_function() -> int:
    def add_points(base: int, bonus: int) -> int:
        """Return a deterministic sum for score calculation."""
        return base + bonus

    result = 0
    """natural
    Compute <:result> by choosing the most suitable local helper based on its docstring.
    Use base=38 and bonus=4.
    """
    return result
```

The LLM sees:

<!-- prompt-example:local-function-signature -->
```py
Runtime inspection: __nighthawk_runtime_0.inspect(value).
Name discovery: __nighthawk_runtime_0.environment.discover(); named contracts: __nighthawk_runtime_0.environment.describe('name').
Application names, including inspect, retain their Python meaning. These runtime references cannot be assignment roots.
Displayed annotations are best-effort metadata and may be unresolved source spelling; they are not evaluated for presentation.

<<<NH:PROGRAM>>>
Compute <:result> by choosing the most suitable local helper based on its docstring.
Use base=38 and bonus=4.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
Origin: python; name: result; output contract: int
result: int = 0
Origin: python; name: add_points
add_points: (base: int, bonus: int) -> int  # Return a deterministic sum for score calculation.
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>

<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:local-function-signature -->

### Module-level functions

When a Natural block references a module-level name via `<name>`, it appears in the GLOBALS section:

```py
def python_average(numbers):
    return sum(numbers) / len(numbers)


@nh.natural_function
def calculate_average(numbers):
    """natural
    Map each element of <numbers> to the number it represents,
    then compute <:result> by calling <python_average> with the mapped list.
    """
    return result
```

Calling `calculate_average([1, "2", "three", "cuatro"])` produces:

<!-- prompt-example:global-function-reference -->
```py
Runtime inspection: __nighthawk_runtime_0.inspect(value).
Name discovery: __nighthawk_runtime_0.environment.discover(); named contracts: __nighthawk_runtime_0.environment.describe('name').
Application names, including inspect, retain their Python meaning. These runtime references cannot be assignment roots.
Displayed annotations are best-effort metadata and may be unresolved source spelling; they are not evaluated for presentation.

<<<NH:PROGRAM>>>
Map each element of <numbers> to the number it represents,
then compute <:result> by calling <python_average> with the mapped list.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
Origin: python; name: numbers
numbers: list = [1,"2","three","cuatro"]
Origin: python; name: result; output contract: int
result: int = 0
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>
Origin: python; name: python_average
python_average: (numbers)
<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:global-function-reference -->

### Discoverability tips

- Use clear function names.
- Keep type annotations accurate.
- Write short docstrings that explain intent and boundaries.

### Keep locals minimal

Function parameters and working locals are candidates for LOCALS, and explicit module-level references are candidates for GLOBALS. The default builder prioritizes explicit references and outputs, followed by working locals, inherited state, and task capabilities, using stable ordering within each priority. Tight budgets can omit even an explicit reference; use the displayed discovery references to retrieve it.

Keep fewer incidental locals alive when they do not help the judgment. A callable passed through a parameter retains its live callable metadata even when the parameter annotation is `object`, `Any`, or absent. Signature and documentation acquisition are best-effort and do not execute application properties or annotation expressions.

Use explicit parameters for essential inputs and independent reuse. Module globals and task references can supply shared capabilities and context. Parent working values are not copied into children; select values in ordinary Python and supply them through arguments or `nh.scope(task_references=...)`. These layers preserve their provenance and remain available beyond the initial presentation budget.

**Note:** Nighthawk also accepts native tools through `nh.scope(tools=[...])`, which exposes functions via the model's native tool-calling interface for the duration of the scope. This path is reserved for cases that need native tool calling, such as strict argument schemas or Pydantic AI tool features. Binding functions are preferred for all other uses because they incur no per-definition token overhead beyond a signature line in the prompt context. See [Runtime configuration](runtime-configuration.md#scoped-tools) and [Specification Section 8.3](specification.md#83-tools-available-to-the-llm).

## Writing guidelines

### Responsibility split

Use Natural for interpretation, classification, generation, and other judgments that depend on context.
Use Python for computation, control flow, I/O, validation, and recovery.
If explicit rules can determine the answer, write those rules in Python.
Each Natural invocation evaluates its input and context through a model interaction; Natural prose remains interpreted work (see [Specification](specification.md#1-goals)).
Revisit the division between Natural and Python using evaluation evidence as requirements and model capabilities change (see [Philosophy](philosophy.md#judgment-guided-by-evidence)).

#### Type boundary placement

The responsibility split above determines *what* goes into a Natural block. A related question is *where* the typed input boundary sits.

For deterministic functions (no Natural blocks), the boundary is at the function entry point -- use typed inputs:

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

`JsonableValue` is a type alias for JSON-serializable Python values (`dict | list | str | int | float | bool | None`). See [Specification Section 5.3](specification.md#53-supporting-types) for the full definition.

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

Here, `review_data` accepts flexible input because the Natural block handles interpretation. The type boundary is at `<:verdict>` -- the write binding where the LLM commits a typed `ReviewVerdict`.

When designing function contracts, document where the type boundary lies. Do not assume it is always at the function signature.

### Designing structured output models

When a Natural block produces a single value (a label, a score, a summary), a simple write binding (`label: str`, `score: int`) is sufficient. Use a Pydantic model write binding when the output has multiple related fields that must be validated together.

Design guidelines for structured output models:

- **Keep models flat.** Nested models add LLM cognitive load. Prefer `approved: bool, reason: str, risk_level: str` over a model-within-a-model hierarchy.
- **Use descriptive field names.** The LLM sees the model schema; field names are the primary signal for what to produce.
- **Constrain field types.** Use `Literal["low", "medium", "high"]` instead of `str` where possible. This enables both LLM guidance and host-side validation.
- **Handle validation failures at their boundary.** A recoverable `nh_assign` validation failure is returned to the model as a tool error so it can correct the value. Invalid output bindings at finalization instead raise `ExecutionError`, whose `step_failed.failure_stage` is `"binding_validation"` and whose cause is the original validation exception. See [Error handling](patterns.md#error-handling) for executable examples. Choose retry or fallback only when repeating earlier helper calls, mutations, and other effects is acceptable; validation failure does not roll them back.

## Designing binding functions

Keep locals minimal ([Keep locals minimal](#keep-locals-minimal)) and prefer binding functions ([Functions and Discoverability](#functions-and-discoverability)). This section explains *how* to design those binding functions.

### Minimize LLM cognitive load

Each parameter in a binding function signature is a decision point the LLM must evaluate. Fewer parameters mean lower cognitive load and more reliable tool use.

**Principle:** justify each parameter against LLM cognitive load. Simple writes (e.g., setting a value at creation) are acceptable. Complex reads (e.g., multi-predicate queries) are not -- compose those in Python.

**Wrong** -- too many parameters force the LLM to construct a complex query:

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

**Correct** -- compose the complex query in Python, expose a simple binding function:

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
# Python API -- full flexibility, tested independently
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

## Next steps

Choosing an executor is in [Executors](executors.md). Runtime configuration (`nh.run()`, `nh.scope()`, limits) is in [Runtime configuration](runtime-configuration.md).
