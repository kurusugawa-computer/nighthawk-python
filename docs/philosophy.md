# Philosophy

Python owns the control flow. The LLM works inside typed blocks, receiving inputs and returning outputs through explicit bindings.

## Execution model

Nighthawk embeds Natural blocks inside ordinary Python functions. Each block has a typed boundary. Read bindings (`<name>`) pass Python values in. Write bindings (`<:name>`) pass results back out, validated against their type annotations. Binding functions let the LLM call Python functions during execution. Python controls the sequencing -- loops, conditionals, error handling, retries -- and the LLM operates inside each block with no implicit message history carried across blocks.

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

calculate_average([1, "2", "three", "cuatro", "五"])  # 3.0
```

Binding functions like `<python_average>` appear in the prompt as a compact signature line. The LLM's pre-trained Python knowledge lets it reason about types, return values, and composition from the signature alone, without JSON Schema or protocol overhead. See [Tool exposure efficiency](#tool-exposure-efficiency) for the quantitative comparison with MCP and CLI tool exposure.

With provider-backed executors, each Natural block is a single LLM call. A sentiment classifier whose write binding is typed as `Literal["positive", "negative", "neutral"]` rejects any output outside the declared set -- Pydantic validates the type annotation at runtime, not as a hint. The same mechanism applies to numeric extraction (`int`, `float`), structured parsing (Pydantic models), and any task where the judgment space is bounded. Because the host program owns the loop, a misclassified result can be retried, logged, or routed to a fallback -- all in ordinary Python.

With [coding agent backends](coding-agent-backends.md), the same boundary contract applies, but each Natural block becomes an autonomous agent execution. The agent can read files, run commands, and invoke skills -- while typed bindings enforce what crosses the boundary back to Python. The same `scope()` and `run()` context managers that structure human-written workflows are equally legible to a coding agent constructing workflows programmatically. When a coding agent operates inside a Natural block, binding functions appear as Python signatures in the prompt:

```
fetch_items: (category: str, limit: int = 10) -> list[Item]
merge_results: (primary: list[Item], secondary: list[Item]) -> list[Item]
```

The underlying LLM's pre-trained Python knowledge lets it infer that `Item` has attributes, that the return value supports iteration and indexing, and that `merge_results` accepts the output of `fetch_items` directly -- all from the type annotations alone. A CLI tool description (`fetch-items --category X --limit 10`) is optimized for invocation syntax; output structure is left to the model's training data.

Coding agent backends make this especially practical because the agent can immediately apply that inferred structure while reading workflow code, invoking tools, editing implementations, running `pytest`, and iterating within the same Python codebase. The agent works directly in Python with standard tooling -- debugger, test runner, type checker -- rather than through a separate orchestration layer.

## The harness matters more than the model

The strongest direct evidence comes from agentic coding tasks. The subsections below separate what has been measured from where Nighthawk extends the principle.

### Observed evidence

Empirical evidence suggests the surrounding program matters more than the model. Can Boluk's [2026 experiment](https://blog.can.ac/2026/02/12/the-harness-problem/) tested 16 models across 3 edit tools on 180 tasks: changing only the harness improved one model's success rate from 6.7% to 68.3% -- a tenfold gain with no model change. LangChain reported a similar pattern (2026), improving their coding agent from 52.8% to 66.5% accuracy through harness changes alone.

Mitchell Hashimoto [named the practice "harness engineering"](https://mitchellh.com/writing/my-ai-adoption-journey) in February 2026: "Anytime you find an agent makes a mistake, you take the time to engineer a solution such that the agent never makes that mistake again." OpenAI published a [detailed account of harness-first development](https://openai.com/index/harness-engineering/) the same month.

The direct evidence concerns LLM-driven code editing and file management tasks, where harness design (edit format, tool configuration, context management) produced larger gains than model selection. These tasks involve multi-step tool use and file manipulation, which differs structurally from single-turn classification or extraction.

### Design inference for Nighthawk

We think the same principle applies to provider-backed judgments like sentiment classification and numeric interpretation, but we have not measured it directly. Typed bindings limit what the LLM can return, and resilience transformers handle transient failures -- both should help, but neither has been tested in the same controlled way as the coding-task evidence above.

Regardless of scope, the practical question is how harness improvements are expressed. Configuration-file guardrails -- rule files, lifecycle hooks, permission modes, tool filtering -- are effective at restricting behavior. They are optimized for static constraints. Dynamic orchestration (conditional retries, typed input/output contracts, scope-dependent tool visibility, prompts that adapt at runtime) requires a programming language, which is where Nighthawk's Python-first approach fits.

The primitives described in the [Execution model](#execution-model) and the following sections -- typed bindings, resilience transformers, scoped execution contexts -- are how Nighthawk implements the principle in Python.

## Design consequences

The sections below explore what follows from the typed-binding execution model: resilience, scoping, tool exposure, multi-agent coordination, and the tradeoffs the design accepts.

### Resilience as composable functions

Production LLM applications need strategies for transient failures, unstable outputs, and provider outages. Workflow engines build retry, checkpointing, and human-in-the-loop into the graph runtime. Nighthawk takes a different approach. Resilience primitives (`nighthawk.resilience`) are ordinary Python function transformers that wrap any callable. Each transformer takes a function and returns a new function with the same signature. Retry, fallback, voting, timeout, and circuit breaker logic composes by nesting -- no graph DSL, no framework-managed state, and no implicit retry policy. The host controls exactly which calls are retried, how many times, and what happens on exhaustion -- using the same Python debugger, pytest, and code review workflows as the rest of the application. This applies equally to lightweight provider-backed judgments and autonomous agent executions. See [Patterns](patterns.md#resilience-patterns) for usage patterns and composition examples.

### Scoped execution contexts

`run()` establishes the execution boundary: it links a step executor to the current context as an explicit Python `with` statement rather than as a global configuration or implicit thread-local. `scope()` narrows configuration within an existing run -- model override, prompt suffix, or executor replacement -- each taking effect only within the nested `with` block. Nesting is natural Python lexical scoping: the host program's control flow, not a framework runtime, determines which configuration is active at any point. Runtime behavior lives in Python structures rather than in prose-only instructions or static configuration. See [Runtime configuration](runtime-configuration.md) for details and examples.

### Tool exposure efficiency

Binding functions carry higher information density per token than JSON Schema or CLI descriptions (see [Execution model](#execution-model) for how they appear in the prompt). This section compares the per-tool context cost across approaches.

MCP tool definitions carry per-request JSON Schema overhead that grows with the number of exposed tools. CLI tools reduce definition overhead but carry hidden costs -- Mario Zechner's [2025 benchmark](https://mariozechner.at/posts/2025-08-15-mcp-vs-cli/) found that CLI invocations in Claude Code trigger per-command security classification that consumed an order of magnitude more tokens than equivalent MCP calls. In both approaches, substantial context budget is spent on tool plumbing before the model sees the actual task.

**MCP** defines tools as JSON Schema objects served over a protocol layer. Each tool definition consumes tokens in every request.

**CLI tools** improve substantially by leveraging the LLM's pre-trained knowledge of shell commands. An equivalent CLI tool's README can describe the same capabilities in as few as 225 tokens. However, CLIs operate on untyped string I/O: structured data must be serialized to text and parsed back, type safety depends on convention rather than enforcement, and testing requires shell-level scaffolding. Because CLI output structure is undeclared, the LLM must infer it from training data -- making multi-step tool composition dependent on probabilistic recall rather than structural guarantees.

**Nighthawk binding functions** take the CLI insight one step further. LLMs know Python just as well as they know bash. A binding function appears in the prompt as a single signature line:

```
find_top_items: (category: str) -> list[dict]  # Return the highest-scored recent items in a category.
```

The type annotations let the LLM reason structurally: a `list[dict]` return supports iteration and key access, an `Item` return type has discoverable attributes, and typed parameters make it clear what another binding function will accept. There is no protocol layer, no serialization boundary, and no per-tool JSON Schema overhead. The same type annotations serve as targets for optional static analysis (pyright) and as hooks for Nighthawk's runtime validation (via Pydantic). Testing, debugging, and composition use standard Python tooling.

| Approach | Per-tool context cost | Information density | Type safety | Composability | Testing | Interoperability |
|---|---|---|---|---|---|---|
| MCP | High (JSON Schema per tool) | Low (verbose schema) | Schema-level | Framework-dependent | Framework-specific | Cross-language standard |
| CLI | Low (pre-trained knowledge) | Medium (output inferred) | None (string I/O) | Pipes (linear, string-based) | Shell scripts | Universal (any runtime) |
| Binding functions | Low (one signature line) | High (types + semantics) | Annotation-based (static analysis + write-boundary runtime enforcement) | Native (function composition) | pytest | Python-only |

### Multi-agent coordination without a framework

Multi-agent systems face three structural challenges: how agents communicate state, how agents are isolated from each other, and how results from multiple agents are merged. Existing workflow engines address these through framework-specific mechanisms -- graph state for communication, managed runtimes for isolation, message aggregation for merging -- but each ties communication, isolation, and merging to the framework's own abstractions.

Nighthawk is not a multi-agent framework. It is a building block that composes with Python's existing ecosystem for each challenge.

**Communication.** Between Natural blocks within a function, Python variables carry state forward -- read bindings (`<name>`) expose values, write bindings (`<:name>`) commit new values with type validation. Between Natural functions, communication is ordinary Python: return values, function arguments, shared data structures. No message broker, no graph state, no framework-managed channels. For cross-process or distributed coordination, any Python-native mechanism (asyncio, queues, task brokers) can orchestrate Natural function calls, because they are ordinary Python callables.

**Isolation.** Nighthawk provides logical isolation at binding boundaries: read bindings prevent name rebinding, write bindings are type-validated, and each Natural block executes with an independent step context carrying no implicit message history. Read bindings do not prevent in-place mutation of mutable objects -- this is intentional and underlies the [carry pattern](patterns.md#the-carry-pattern). OS-level isolation -- sandboxing, filesystem scoping, permission control -- is delegated to the execution backend. Coding agent backends provide their own sandbox modes and working directory scoping, which Nighthawk configures but does not reimplement.

**Result merging.** The resilience module provides composable patterns for common cases: `vote` for majority consensus across repeated invocations, `fallback` for sequential first-success chaining. Domain-specific merging -- reconciling edits from multiple agents, aggregating heterogeneous outputs, resolving conflicts -- belongs in user code, because merge semantics are inherently domain-dependent. Nighthawk ensures that each agent's output crosses the boundary as a typed, validated Python object that merge logic can operate on directly.

### Tradeoffs

The boundary-centric design has costs:

- **Python lock-in.** Binding functions, type annotations, and resilience transformers are Python constructs. Nighthawk does not offer a language-neutral protocol; interoperability with non-Python systems requires explicit bridging (e.g., REST endpoints wrapping Natural functions).
- **Per-invocation cost.** Every Natural block invocation calls the LLM. There is no compilation step that amortizes cost across inputs. For high-throughput, low-judgment tasks where a deterministic Python function would suffice, a Natural block is the wrong tool. See [Why evaluate every time](#why-evaluate-every-time) for the design rationale.
- **Integration tests are essential.** Mock tests verify Python logic around Natural blocks, but verifying that the LLM produces correct judgments requires integration tests against a real provider. The [two-layer testing strategy](verification.md) is not optional -- because the LLM produces the judgment, only a real LLM call can verify it.
- **Manual orchestration burden.** Nighthawk leaves branching, retries, merge logic, and recovery policy in user code rather than a graph runtime. This is a direct cost of the "Python controls all flow" principle.
- **Python API design discipline.** Binding functions are only as effective as their signatures, type annotations, and naming. Poor API design degrades the LLM's ability to reason about composition.

## Why evaluate every time

A natural question: why not use an LLM once to translate a Natural block into equivalent Python code, then run the generated code on every invocation? This would eliminate per-call latency, cost, and non-determinism.

The answer is that Natural blocks exist precisely for tasks that cannot be reduced to deterministic code. "Classify the sentiment of this review" or "interpret this ambiguous user input" require judgment that depends on the specific input, world knowledge, and context. If a task could be written as deterministic Python, it should be -- this is the core design principle (see [Natural blocks](natural-blocks.md#responsibility-split)).

One-time compilation has additional limitations:

- The generated code would freeze the LLM's world knowledge at compilation time.
- The input space is unbounded: "three apples, a dozen eggs, and cinco naranjas" requires open-ended interpretation that no finite code generation can fully anticipate.
- Verifying the correctness of the generated code ultimately requires an LLM -- creating a circular dependency.

With [coding agent backends](coding-agent-backends.md), "evaluate every time" means launching an autonomous agent for each Natural block invocation, with full adaptability to the specific input. The per-invocation cost is higher, but so is the adaptability.

Nighthawk addresses the reliability concern through constraints rather than compilation: type validation on write bindings, deny frontmatter to restrict allowed outcomes, structured outcome kinds for control flow, and a [two-layer testing strategy](verification.md) (mock tests for Python logic, integration tests for Natural block effectiveness).

## The design landscape

Three positions in the current design space illustrate the range of orchestration approaches: orchestration frameworks, literate-programming-style harnesses, and Nighthawk.

### Orchestration frameworks

**LangGraph, CrewAI, and AutoGen.** The LLM or graph runtime decides what happens next -- which tools to call, how to route between agents, when to stop. Contracts are enforced through framework-managed schema, guardrails, and routing conditions. State flows through the graph as messages, and conversation history accumulates implicitly across steps.

### Literate-programming-style harnesses

**Agent Skills and similar approaches.** Orchestration logic lives outside the host program's type system -- in natural language instructions with embedded code for strict procedures. Constraints are expressed in natural language and enforced probabilistically. When orchestration and state live in natural language, synchronizing execution state between the prompt world and the code world is hard. The following example illustrates the state synchronization challenge:

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

The instruction references embedded code, but there is no explicit boundary for how `result` crosses back to the host program. The narrative assumes the value will be available to subsequent steps, but getting `result` back to the host program is implicit -- it depends on convention, not a declared contract.

### Nighthawk

The host Python program owns orchestration. Contracts are expressed through Python types and enforced through runtime validation, structured outcomes, and explicit block boundaries. State lives in Python variables with explicit transfer at block boundaries. See [Execution model](#execution-model) for the full description.

| | Orchestration frameworks | Literate harnesses | Nighthawk |
|---|---|---|---|
| Control | LLM orchestrates via graph/routing | Natural language instructions | Python controls all flow |
| State | Graph state, message history | Embedded in prompt narrative | Python locals, explicit bindings |
| Cross-step context | Implicit (conversation accumulates) | Implicit (prompt continuation) | Explicit (bindings, scoped injection) |
| Debugging | Framework-specific tooling | Prompt inspection | Python debugger, pytest |
| Constraint model | Guardrails, routing conditions | Natural language (probabilistic) | Type validation, deny frontmatter, structured outcomes |

Static constraint systems -- such as AGENTS.md-style rule files, lifecycle hooks, and permission modes -- remain useful as a guardrail layer around any of the approaches above, but they do not replace runtime orchestration or typed state transfer.

Orchestration frameworks are a better fit when multi-agent coordination is the core of the task, or when accumulated conversation history is essential (e.g., chatbots). Literate-programming-style harnesses suit scenarios where orchestration logic is expressed most naturally in prose, or where the target audience writes instructions rather than code. Nighthawk is a better fit when deterministic control flow contains discrete judgment points, when integrating LLM reasoning into an existing Python codebase, or when strict input/output constraints are needed on each judgment.
