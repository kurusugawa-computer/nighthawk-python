# Philosophy

This page details Nighthawk's design positioning: workflow styles, comparison with workflow engines, tool exposure tradeoffs, and the rationale for evaluating Natural blocks at runtime.

## Workflow styles

Three workflow styles sit at different points on the control-vs-flexibility spectrum.

### 1. Python-first (embed Natural blocks for semantics)

The approach explored by [Nightjar](https://github.com/psg-mit/nightjarpy). You write strict flow in Python, and embed Natural blocks where semantics are needed.

Pros:
- Hard guarantees: exact loops, strict conditionals, deterministic boundaries.
- Tools: debuggers, tests, linters, and normal software engineering practices apply.
- The LLM is "physically constrained" to operate on interpreter-visible objects (locals, memory, tool context).

Cons:
- Knowledge often ends up encoded in code-adjacent artifacts, which can be less maintainable by non-engineers.

Example:

```py
@nh.natural_function
def calculate_average(numbers):
    """natural
    Map each element of <numbers> to the number it represents,
    then compute the arithmetic mean as <:result>.
    """
    return result

result = calculate_average([1, "2", "three", "cuatro", "五"])
print(result)  # 3.0
```

### 2. Natural-first (embed code for strict procedures)

Similar in spirit to [Claude Skills](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/skills) and literate programming. You write a natural language workflow first, and embed code only where strict procedures are needed.

Pros:
- Excellent for strategy, iteration, and human collaboration.
- Similar spirit to literate programming: readable narrative with precise code where necessary.

Cons:
- The hard part is state synchronization: how to share and reconcile execution state between the natural language plan/world and the code execution world.

Example:

````md
Compute the "semantic average" of the target list using the following function.
However, the target list contains mixed numeric representations,
so convert the elements appropriately before calling <calculate_average>
and passing them as the argument.

```py
def calculate_average(numbers):
    return sum(numbers) / len(numbers)
```

Target list: `[1, "2", "three", "cuatro", "五"]`

Set <:result> to the computed average.
````

### 3. Interleaved (Python ↔ Natural with tool callbacks)

Nighthawk's execution model is Python-first alternation: Python controls the steps, Natural blocks are inserted where semantic interpretation is needed, and binding functions let the LLM call back into Python.

Example:

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

With [coding agent backends](coding-agent-backends.md), each Natural block becomes an autonomous agent execution. The agent can read files, run commands, and invoke skills -- while Python controls the workflow and bindings enforce typed state synchronization at block boundaries. This addresses the state synchronization challenge of the Natural-first approach: the coding agent operates freely within each block, and only the declared outputs (write bindings with type validation) cross back to Python. See [Coding agent backends](coding-agent-backends.md) for configuration and examples.

## Nighthawk vs workflow engines

Workflow engines like [LangGraph](https://github.com/langchain-ai/langgraph), [CrewAI](https://github.com/crewAIInc/crewAI), and [AutoGen](https://github.com/microsoft/autogen) treat the LLM as the orchestrator. The LLM decides what to do next, which tools to call, and how to route between agents. State flows through the graph as messages, and conversation history accumulates implicitly across steps.

Nighthawk inverts this relationship. Python is the orchestrator. The LLM is a constrained reasoning component that operates inside small Natural blocks, each executing independently with no implicit message history. State lives in ordinary Python variables, not in framework-managed graph state or message buffers.

| | LangGraph / CrewAI / AutoGen | Nighthawk |
|---|---|---|
| Control | LLM orchestrates via graph/routing | Python controls all flow |
| State | Graph state, message history | Python locals, explicit bindings |
| Cross-step context | Implicit (conversation accumulates) | Explicit (carry pattern, f-string injection) |
| Debugging | Framework-specific tooling | Python debugger, pytest |
| Constraint model | Guardrails, routing conditions | Type validation, deny frontmatter, structured outcomes |

Consider the same sentiment classification task. In a workflow engine, you define a graph with nodes and edges, where message history accumulates across calls and the LLM decides the routing:

```py
# Workflow engine style (pseudocode)
graph = StateGraph()
graph.add_node("classify", classify_node)
graph.add_node("route", route_node)
graph.add_edge("classify", "route")
# State is framework-managed; debugging requires framework tooling
```

In Nighthawk, the same task is a single Natural function with typed bindings. No graph, no implicit state:

```py
# Nighthawk style
@nh.natural_function
def classify(text: str) -> str:
    label: str = ""
    """natural
    Read <text> and set <:label> to one of: positive, negative, neutral.
    """
    return label
# State is a Python local; debugging uses pdb and pytest
```

Workflow engines are a better fit when multi-agent coordination is the core of the task, or when accumulated conversation history is essential (e.g., chatbots). Nighthawk is a better fit when deterministic control flow contains discrete judgment points, when you want to integrate LLM reasoning into an existing Python codebase, or when you need strict input/output constraints on each judgment.

## Resilience as composable functions

Workflow engines build retry, checkpointing, and human-in-the-loop into the graph runtime -- resilience is inseparable from the orchestration layer. Nighthawk takes a different approach: resilience primitives (`nighthawk.resilience`) are ordinary Python function transformers that wrap any callable. Retry, fallback, voting, and timeout logic composes with standard Python syntax:

```py
from nighthawk.resilience import retrying, fallback, vote

robust_classify = fallback(
    retrying(attempts=2)(vote(count=3)(classify_gpt4)),
    retrying(attempts=2)(classify_mini),
    default="unknown",
)
```

Each transformer takes a function and returns a function with the same signature. There is no graph DSL, no framework-managed state, and no implicit retry policy. The host controls exactly which calls are retried, how many times, and what happens on exhaustion -- using the same Python debugger, pytest, and code review workflows as the rest of the application. See [Practices Section 5](practices.md#5-resilience-patterns) for usage patterns.

This extends beyond lightweight judgments. With [coding agent backends](coding-agent-backends.md), a Natural block can delegate to an autonomous agent (Claude Code, Codex) that reads files, executes commands, and invokes skills -- while Nighthawk's binding system constrains the inputs and outputs. The same execution model that handles "classify this sentiment" also handles "refactor this module and write tests". Python controls when and how each agent runs; bindings and type validation control what crosses the boundary.

## Tool exposure: MCP, CLI, and binding functions

How tools are exposed to an LLM has a direct impact on context window efficiency. Three approaches sit at different points on the spectrum.

**MCP** defines tools as JSON Schema objects served over a protocol layer. Each tool definition consumes tokens in every request. Mario Zechner's [2025 benchmark](https://mariozechner.at/posts/2025-08-15-mcp-vs-cli/) quantified this cost: GitHub's MCP server exposes 93 tools consuming 55,000 tokens of context. Playwright MCP's 21 tools take 13,700 tokens; Chrome DevTools MCP's 26 tools take 18,000. A browser accessibility tree snapshot consumed 52,000 tokens via MCP, while an equivalent CLI selective query used 1,200 tokens -- a 43x difference. Much of the context window is spent before the model sees the actual task.

**CLI tools** improve substantially by leveraging the LLM's pre-trained knowledge of shell commands. An equivalent CLI tool's README can describe the same capabilities in as few as 225 tokens. The LLM already knows how to use `bash`, so the tool description carries only the delta. However, CLIs operate on untyped string I/O: structured data must be serialized to text and parsed back, type safety depends on convention rather than enforcement, and testing requires shell-level scaffolding. CLI invocations also carry hidden costs -- Claude Code, for instance, performs security checks on each command execution that add token overhead absent from MCP calls.

**Nighthawk binding functions** take the CLI insight one step further. LLMs know Python just as well as they know bash. A binding function appears in the prompt as a single signature line:

```
find_top_items: (category: str) -> list[dict]  # intent: Return the highest-scored recent items in a category.
```

This is roughly 20 tokens -- comparable to the most compact CLI description, but with full type information. There is no protocol layer, no serialization boundary, and no per-tool JSON Schema overhead. Python's type system provides compile-time and runtime validation natively. Testing, debugging, and composition use standard Python tooling.

| Approach | Per-tool context cost | Type safety | Testing |
|---|---|---|---|
| MCP | High (JSON Schema per tool) | Schema-level | Framework-specific |
| CLI | Low (pre-trained knowledge) | None (string I/O) | Shell scripts |
| Binding functions | Minimal (one signature line) | Native (Python types) | pytest |

## Why evaluate every time

A natural question: why not use an LLM once to translate a Natural block into equivalent Python code, then run the generated code on every invocation? This would eliminate per-call latency, cost, and non-determinism.

The answer is that Natural blocks exist precisely for tasks that cannot be reduced to deterministic code. "Classify the sentiment of this review" or "interpret this ambiguous user input" require judgment that depends on the specific input, world knowledge, and context. If a task could be written as deterministic Python, it should be -- this is the core design principle (see [Practices Section 1](practices.md#1-writing-guidelines)).

One-time compilation has additional structural limitations:

- The generated code would freeze the LLM's world knowledge at compilation time.
- The input space is unbounded: "three apples, a dozen eggs, and cinco naranjas" requires open-ended interpretation that no finite code generation can fully anticipate.
- Verifying the correctness of the generated code ultimately requires an LLM -- creating a circular dependency.

With [coding agent backends](coding-agent-backends.md), "evaluate every time" means launching an autonomous agent for each Natural block invocation. The agent can adapt its strategy to the specific input -- reading different files, running different commands, exploring different approaches -- in ways that no pre-compiled code could anticipate. The per-invocation cost is higher, but so is the adaptability.

Nighthawk addresses the reliability concern through constraints rather than compilation: type validation on write bindings, deny frontmatter to restrict allowed outcomes, structured outcome kinds for control flow, and a [two-layer testing strategy](practices.md#3-testing-and-debugging) (mock tests for Python logic, integration tests for Natural block effectiveness).
