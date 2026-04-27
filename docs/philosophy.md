# Philosophy

Nighthawk is built around one constraint: Python owns the control flow, and the LLM works inside explicit typed boundaries.

The host program decides when reasoning happens, which data crosses into the prompt, which tools are visible, how failures are handled, and how results are merged. Natural blocks provide the judgment point. Python provides the surrounding contract.

This page is written for readers deciding whether Nighthawk fits an existing Python system. It explains the execution model, the design landscape Nighthawk occupies, the consequences of a boundary-first approach, and the tradeoffs that follow.

## Execution model

Nighthawk embeds Natural blocks inside ordinary Python functions. Each block has a boundary:

- Read bindings (`<name>`) pass Python values in.
- Write bindings (`<:name>`) pass results back out.
- Type annotations on write bindings guide what the LLM should produce.
- Pydantic validates committed write bindings at step finalization.
- Binding functions let the LLM call Python functions during execution.

Python controls sequencing: loops, conditionals, error handling, retries, fallback, logging, and merge logic. Each Natural block executes with a fresh prompt and no implicit message history carried across blocks.

```py
def python_average(numbers: list[float]) -> float:
    return sum(numbers) / len(numbers)

@nh.natural_function
def calculate_average(numbers: list[object]) -> float:
    result: float = 0.0
    """natural
    Map each element of <numbers> to the number it represents,
    then compute <:result> by calling <python_average> with the mapped list.
    """
    return result

calculate_average([1, "2", "three", "cuatro", "五"])  # 3.0
```

Binding functions like `<python_average>` appear in the prompt as compact Python signatures. The LLM can use its pre-trained Python knowledge to reason about parameters, return values, and simple composition without a JSON Schema tool definition for each binding function. The signature is not a proof of semantic correctness. It is a high-density interface hint that is backed by Python execution and surrounding write-boundary validation.

With provider-backed executors, each Natural block is a single Nighthawk step execution. A sentiment classifier whose write binding is typed as `Literal["positive", "negative", "neutral"]` rejects any committed output outside the declared set. Pydantic validates write bindings at step finalization; the annotation is not merely prompt advice. The same mechanism applies to numeric extraction (`int`, `float`), structured parsing (Pydantic models), and tasks where the judgment space can be bounded.

With [coding agent backends](coding-agent-backends.md), the same boundary contract applies, but each Natural block becomes an autonomous agent execution. The agent can read files, run commands, and invoke backend-native skills while typed bindings enforce what crosses the boundary back to Python. The same `run()` and `scope()` context managers that structure human-written workflows are also legible to coding agents constructing workflows programmatically.

When a coding agent operates inside a Natural block, binding functions appear as Python signatures in the prompt:

```text
fetch_items: (category: str, limit: int = 10) -> list[Item]
merge_results: (primary: list[Item], secondary: list[Item]) -> list[Item]
```

From those annotations, the model can infer useful composition facts: `fetch_items` returns a list, the result can be passed to `merge_results`, and the category and limit arguments have expected types. If the `Item` definition is visible in context, the model can reason about its fields. If it is not visible, the model should treat `Item` as an opaque domain object and use binding functions or visible APIs to inspect it.

When the rendered prompt exceeds context limits, the runtime omits remaining entries from the rendered context and appends a `<snipped>` marker. The underlying data stays in Python memory. Binding functions that retrieve host-side data can still query it at runtime, so truncation reduces prompt load without deleting host-side state.

Because each Natural block is a fresh prompt, the prompt surface is determined by the host program at each invocation: block text, f-string interpolation for inline Natural blocks, bindings, visible globals, and scoped configuration. Changing one invocation does not mutate the implicit memory of another block because there is no implicit block-to-block memory.

## Design landscape

Nighthawk sits between two common approaches: orchestration frameworks and literate-programming-style harnesses.

### Orchestration frameworks

Frameworks such as LangGraph, CrewAI, and AutoGen put orchestration into a graph or agent runtime. The graph runtime executes developer-defined routing; in agentic configurations, the LLM may participate in tool choice or routing. Contracts are enforced through framework-managed schema, guardrails, routing conditions, and graph state.

This is a good fit when multi-agent coordination is the core product, when conversation history is central, or when the application wants a dedicated graph runtime.

### Literate-programming-style harnesses

Agent skills, instruction files, and similar systems express orchestration in natural language with embedded code snippets for strict procedures. They are useful for teaching an agent how to operate inside a project, but their state boundaries are usually conventional rather than typed.

````md
Compute the average using `calculate_average`.
Convert the mixed representations before calling it.

```py
def calculate_average(numbers):
    return sum(numbers) / len(numbers)
```

Target: `[1, "2", "three", "cuatro", "五"]`
Store the computed average in `result`.
````

The instruction names `result`, but it does not declare how that value crosses back into the host program. The prompt narrative assumes the value will be available later. Nighthawk makes that boundary explicit with `<:result>`.

### Nighthawk

Nighthawk keeps orchestration in Python. State lives in Python variables and crosses block boundaries through bindings. Contracts are expressed through Python types and enforced through runtime validation, structured outcomes, deny frontmatter, and explicit block boundaries.

| | Orchestration frameworks | Literate harnesses | Nighthawk |
|---|---|---|---|
| Control | Graph or agent runtime | Natural language instructions | Python control flow |
| State | Graph state, message history | Prompt narrative | Python locals, explicit bindings |
| Cross-step context | Often implicit | Often implicit | Explicit bindings and scoped injection |
| Debugging | Framework-specific tooling | Prompt inspection | Python debugger, pytest, tracing |
| Constraint model | Guardrails, schemas, routing conditions | Natural language conventions | Type validation, deny frontmatter, structured outcomes, scoped oversight |

Static constraint systems such as AGENTS.md-style rule files, lifecycle hooks, and permission modes remain useful around all three approaches. They are guardrail layers. They do not replace runtime orchestration or typed state transfer.

## Design consequences

Nighthawk's central claim is a design claim, not a benchmark claim. When LLMs perform discrete judgments inside a larger program, Nighthawk treats the host-side contract as a primary reliability lever alongside model and prompt choice: what crosses into the prompt, what crosses back, which tools are visible, and how failures recover.

Recent agentic coding work has independently surfaced this principle under names such as harness engineering -- see Mitchell Hashimoto's ["Engineer the Harness"](https://mitchellh.com/writing/my-ai-adoption-journey) framing and OpenAI's ["Harness engineering"](https://openai.com/index/harness-engineering/) writeup. Nighthawk applies it to ordinary Python applications by making the host-side contract explicit and typed.

For provider-backed judgments, typed bindings constrain what can be committed, resilience transformers handle retry and fallback, and Python control flow handles routing. For coding-agent backends, the Natural block becomes a bounded autonomous execution inside the same contract.

Whether this approach pays off for a given workload is an empirical question, answered by integration tests against the actual task, provider, model, prompt, and data distribution. The framework's contribution is structural: a place to express dynamic orchestration that combines typed contracts, deterministic control flow, tests, tracing, and domain logic in one system.

The boundary-first execution model leads to several practical consequences.

### Resilience as composable functions

Production LLM applications need strategies for transient failures, unstable outputs, provider outages, and bad judgments. Workflow engines often build retry, checkpointing, and human-in-the-loop behavior into a graph runtime.

Nighthawk keeps resilience in Python. The `nighthawk.resilience` primitives are ordinary function transformers. Each transformer takes a callable and returns a callable with the same signature. Retry, fallback, voting, timeout, budget, and circuit breaker logic compose by wrapping functions rather than by entering a separate graph DSL.

The host controls exactly which calls are retried, how many attempts are allowed, which exceptions trigger fallback, what gets logged, and what happens on exhaustion. This applies to lightweight provider-backed judgments and to autonomous coding-agent executions. See [Patterns](patterns.md#resilience-patterns) for examples.

### Scoped execution contexts

`run()` establishes the execution boundary by linking a step executor to the current context through an explicit Python `with` statement. `scope()` narrows configuration within an existing run: model override, prompt suffix, executor replacement, implicit references, or oversight.

Nesting follows Python lexical structure. The host program's control flow, not a framework runtime, determines which configuration is active at any point. Runtime behavior lives in Python structures rather than in prose-only instructions or static configuration. See [Runtime configuration](runtime-configuration.md) for details.

### Tool exposure efficiency

Binding functions are efficient when the tool is already a Python callable in the host process. They appear as signature lines rather than as per-request JSON Schema tool definitions:

```text
find_top_items: (category: str) -> list[dict]  # Return the highest-scored recent items in a category.
```

This has three advantages:

- The prompt cost is low.
- Type annotations provide composition hints.
- Testing and debugging use normal Python tools.

This does not make MCP or CLI tools obsolete. MCP is useful for cross-process and cross-language tool exposure, especially when a backend expects MCP. CLI tools are universal and often excellent when the tool already has a clear command-line interface. Mario Zechner's [2025 MCP vs CLI benchmark](https://mariozechner.at/posts/2025-08-15-mcp-vs-cli/) found that the protocol itself was not the decisive factor in the tested cases; tool design, output cleanliness, security checks, and documentation quality mattered heavily.

The Nighthawk position is narrower: for Python-local helper functions, a binding function is the shortest path from host logic to LLM-usable capability.

| Approach | Best fit | Context cost | Type boundary | Composition | Interoperability |
|---|---|---|---|---|---|
| MCP | Cross-process or backend-native tools | Often high, depends on schema design | Schema-level | Protocol and framework dependent | Cross-language |
| CLI | Existing command-line tools | Low to medium, depends on docs and output | String I/O by convention | Shell composition | Universal |
| Binding functions | Python-local helper functions | Low signature-line cost | Python annotations plus write-boundary validation | Native function composition | Python-only |

### Multi-agent coordination without a framework

Nighthawk is not a multi-agent framework. It is a building block that composes with Python's existing coordination mechanisms.

**Communication.** Between Natural blocks within a function, Python variables carry state forward. Read bindings expose values, write bindings commit validated values, and function return values connect larger units. For distributed work, ordinary Python mechanisms such as `asyncio`, queues, task brokers, or service calls can orchestrate Natural functions because they are ordinary callables.

**Isolation.** Nighthawk provides logical isolation at binding boundaries. Read bindings prevent name rebinding, write bindings are type-validated, and each Natural block has an independent step context with no implicit message history. Read bindings do not prevent in-place mutation of mutable objects; this is intentional and enables the [carry pattern](patterns.md#the-carry-pattern). OS-level isolation, filesystem permissions, and sandboxing are delegated to the execution backend.

**Result merging.** The resilience module provides common aggregation patterns such as `vote` and `fallback`. Domain-specific merging belongs in user code because merge semantics are domain-specific. Nighthawk ensures that each result crosses the boundary as a typed Python object that merge logic can inspect and validate.

## Trust and safety boundaries

Typed bindings are output and state-transfer boundaries. They are not a sandbox for arbitrary untrusted instructions.

Natural DSL sources, imported markdown, and prompt fragments are part of the program. They define the execution boundary itself: which bindings are visible, how the prompt is structured, which tools the model may invoke, and which write types it may commit. Treating them as trusted, repository-managed assets is not a convenience but a consequence of their role -- a change to a Natural source is a change to the program's contract with the model, comparable to editing a function signature or a route definition.

User input has a different role. It is data the program processes, not structure the program declares. Pass user input through bindings, where it is rendered as data and constrained by typed write boundaries. Do not interpolate it into Natural source text or template preprocessing, where it would merge with executable prompt structure. Inline f-string Natural blocks evaluate Python expressions before prompt construction, so the same rule applies to f-string interpolation.

Coding-agent backends can read files, run commands, invoke skills, and use backend-native tools according to their configuration. Nighthawk configures those backends but does not replace their sandbox, permission, or operating-system isolation mechanisms. Use backend permissions, working-directory scoping, and project instruction files as an outer guardrail layer.

## Tradeoffs

The boundary-centric design has real costs:

- **Python lock-in.** Binding functions, type annotations, scopes, and resilience transformers are Python constructs. Nighthawk does not offer a language-neutral orchestration protocol.
- **Per-invocation cost.** Every Natural block invocation calls an LLM or launches an agent execution. Deterministic work should stay in Python.
- **Manual orchestration burden.** Branching, retry policy, merge logic, recovery, and cancellation remain user-code responsibilities rather than graph-runtime responsibilities.
- **Integration tests are essential.** Mock tests verify Python logic around Natural blocks. Real LLM behavior requires integration tests against the provider and model you will use. See [Verification](verification.md).
- **API design discipline.** Binding functions are only as useful as their names, signatures, type annotations, and docstring intent.
- **Backend-dependent isolation.** Nighthawk validates what crosses back into Python, but backend sandboxes control what an autonomous agent may do while executing.

## Why evaluate every time

A natural question is why Nighthawk does not ask an LLM to compile a Natural block into deterministic Python once, then reuse that generated code for every invocation.

Natural blocks exist for tasks that cannot be reduced to deterministic code without losing the reason to use an LLM. "Classify the sentiment of this review" and "interpret this ambiguous user input" depend on the specific input, language understanding, world knowledge, and context. If a task can be written as deterministic Python, it should be.

One-time compilation has additional limitations:

- The generated code freezes the model's world knowledge at compilation time.
- The input space remains open-ended.
- Verifying the generated code for semantic judgment usually requires another judgment system.

With coding-agent backends, evaluating every time means launching an autonomous agent for each Natural block invocation. The cost is higher, but the execution can adapt to the specific repository state, files, tests, commands, and task context.

Nighthawk addresses reliability through constraints rather than compilation: type validation on write bindings, deny frontmatter, structured outcome kinds, scoped execution, resilience wrappers, and a [two-layer testing strategy](verification.md).
