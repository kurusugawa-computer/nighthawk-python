# Natural blocks
<!-- prompt-example markers (e.g. prompt-example:basic-binding) are test anchors used by tests/docs/test_prompt_examples.py to verify that documented prompt examples match actual rendering output. -->

> This page assumes you have completed [Quickstart](quickstart.md).

This page covers what Natural blocks are and how to design them, from prompt structure through binding functions and structured output.

## Running the examples

All examples assume the [Quickstart](quickstart.md) setup:

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-nano"),
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
<<<NH:PROGRAM>>>
Read <text> and update <:priority> with one of: low, normal, high.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
priority: str = "normal"
text: str = "Server is on fire!"
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>

<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:basic-binding -->

Three sections:

- **`<<<NH:PROGRAM>>>`** -- your Natural block text (after sentinel removal and `textwrap.dedent`).
- **`<<<NH:LOCALS>>>`** -- step locals, rendered alphabetically as `name: type = value`.
  Nighthawk renders the full current locals snapshot by design; control context size by deciding which locals exist before the block.
- **`<<<NH:GLOBALS>>>`** -- module-level names referenced via `<name>` that are not in step locals.

### One block, one task

Each Natural block performs one task -- one set of input bindings, one set of output bindings, one outcome -- and executes independently. There is no implicit message history between blocks. The LLM sees only the prompt assembled for that specific block. Cross-block context must be explicit (see [Patterns: cross-block composition](patterns.md#cross-block-composition)).

## Providing data to a block

Two mechanisms supply data to a Natural block: **bindings** and **f-string injection**.

### Read bindings (`<name>`)

A read binding makes a Python value visible in the LOCALS section. The name cannot be rebound by the LLM.

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

Type annotations on write bindings enable validation and coercion at step finalization for values that are actually committed.

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

When the LLM assigns a value to `verdict`, Nighthawk validates and coerces it to a `ReviewVerdict` instance. If the value does not conform to the model schema, a `ToolValidationError` is raised.

**How write bindings appear in the prompt.** A pre-declared write binding with an initial value appears in LOCALS like any other local (e.g., `sentiment: str = "neutral"`). An annotation-only or undeclared write binding does not appear in LOCALS -- the LLM discovers it from the `<:name>` reference in the program text.

### Prompt appearance of bindings

Read and write bindings are rendered identically in the LOCALS section (e.g., `name: type = value`). The `<name>` vs `<:name>` distinction in the Natural program text is the signal that tells the LLM which names it may update. At runtime, Nighthawk enforces the distinction: read bindings block rebinding, while write bindings allow rebinding and commit values back to Python locals.

### Multimodal bindings

A binding may hold a Pydantic AI multimodal value -- `BinaryContent`, `ImageUrl`, `AudioUrl`, `DocumentUrl`, `VideoUrl`, or `UploadedFile`. These are first-class binding values: reference them with `<name>` like any other binding, and Nighthawk renders the inline placeholder (`<image>` for image media, `<file>` otherwise) in the LOCALS or GLOBALS line. Explicit dotted leaf references use the same mechanism, and dotted `list` / `tuple` leaves are hoisted when they satisfy the same `UserContent` rule as top-level bindings. `UploadedFile` is only usable as native user-prompt content on provider-backed executors that can resolve the provider-owned file reference; coding-agent backends reject it at the user-prompt boundary.

Transport depends on the executor:

- Provider-backed executors that accept Pydantic AI `UserContent` send the value natively to the VLM API.
- Coding-agent backends text-project the value (staged local files or URL references). See [Coding agent backends: multimodal inputs](coding-agent-backends.md#multimodal-inputs).

For the normative rendering and transport rules, including dotted-reference hoisting and the remaining v0.11.0 nesting restrictions, see [Specification Section 8.2](specification.md#82-locals-summary).

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
<<<NH:PROGRAM>>>
Read <post>.
Available policies: ['safety-first', 'concise-output', 'cite-assumptions']
Select the single best policy and set <:selected_policy>.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
post: str = "Breaking: earthquake hits downtown"
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
<<<NH:PROGRAM>>>
Compute <:result> by choosing the most suitable local helper based on its docstring.
Use base=38 and bonus=4.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
add_points: (base: int, bonus: int) -> int  # Return a deterministic sum for score calculation.
result: int = 0
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
<<<NH:PROGRAM>>>
Map each element of <numbers> to the number it represents,
then compute <:result> by calling <python_average> with the mapped list.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
numbers: list = [1,"2","three","cuatro"]
result: int = 0
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>
python_average: (numbers)
<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:global-function-reference -->

### Discoverability tips

- Use clear function names.
- Keep type annotations accurate.
- Write short docstrings that explain intent and boundaries.

### Keep locals minimal

Function parameters and local variables appear in LOCALS. Module-level names referenced via `<name>` that are _not_ in locals appear in GLOBALS. Nighthawk renders callable entries with their full signature and docstring intent -- but only when type information is available.

This is a deliberate tradeoff: LOCALS stays a flat, predictable snapshot of what Python has already bound. If you want less prompt context, keep fewer locals alive at the block boundary. Section truncation still follows the normal lexicographic rendering order, so under tight budgets an explicitly referenced dotted multimodal leaf can still be omitted along with any other later entry.

When you pass a module-level callable as a function parameter with a generic type (`object`, `Any`, or no annotation), the name moves from GLOBALS to LOCALS and **its signature is erased**. The LLM cannot discover the correct arguments or return type.

The principle extends beyond callables. Any module-level name that is stable across invocations -- constants, classes, utility functions -- should stay in GLOBALS via `<name>` read bindings rather than being pulled into LOCALS via parameters or local assignments. Reserve function parameters for data that genuinely varies per call.

**Note:** Nighthawk also provides `@nh.tool`, which registers functions via the model's native tool-calling interface. This path is reserved for cases that require `RunContext[StepContext]` access. Binding functions are preferred for all other uses because they incur no per-definition token overhead beyond a signature line in the prompt context. See [Specification Section 8.3](specification.md#83-tools-available-to-the-llm) for the `@nh.tool` specification.

## Writing guidelines

### Responsibility split

**Use Natural when the task requires judgment** -- decisions that depend on interpretation, world knowledge, or subjective evaluation:

- Classification and routing (e.g., categorize a support ticket).
- Text generation (e.g., summarize, draft, translate, reformulate).
- Interpretation of ambiguous or unstructured input.
- Selection among options based on context (e.g., choose the best policy).

**Use Python for everything deterministic** -- operations whose result is fully determined by the input:

- Computation (arithmetic, string manipulation, data transformation).
- Control flow (loops, conditionals, sequencing of Natural blocks).
- I/O and side effects (file operations, API calls, database queries).
- Validation, type enforcement, and error recovery.
- State management and data flow between Natural blocks.

**Decision rule:** if the correct output can be computed without an LLM, use Python. Natural blocks add latency, cost, and non-determinism -- reserve them for tasks that genuinely require LLM capabilities.

A corollary: do not attempt to "compile" a Natural block into equivalent Python code via a one-time LLM translation. Natural blocks exist for tasks whose correct output depends on interpretation, world knowledge, and context that cannot be captured in static code. If the task could be reduced to deterministic Python, it should be written in Python from the start. See [Philosophy](philosophy.md#why-evaluate-every-time) for the full rationale.

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
- **Handle validation failures.** When the LLM produces a value that fails Pydantic validation, Nighthawk raises `ToolValidationError`. Wrap the Natural function call with `retrying` from `nighthawk.resilience` to retry on validation errors, or use `fallback` to fall back to a simpler output type.

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
