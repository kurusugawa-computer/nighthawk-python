# Philosophy

Nighthawk is built around one constraint: Python owns the control flow, and the LLM works inside explicit typed boundaries.

The host decides when reasoning happens, how context is presented, and how results and failures are handled.
Natural blocks supply judgment using Python values and inherited task context.
This division supports small classifications and autonomous agent tasks within the same execution model.

## Execution model

Nighthawk embeds Natural blocks inside ordinary Python functions. Each block has a boundary:

- Read bindings (`<name>`) identify required values and prioritize their presentation; they are not an access allowlist.
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

Binding functions appear as compact Python signatures that guide composition.
Pydantic validates output bindings at finalization: a `Literal["positive", "negative", "neutral"]` result must belong to that set, though validation cannot establish the judgment's factual accuracy.

[Coding agent backends](coding-agent-backends.md) apply the same output boundary to an autonomous execution that can inspect files, run commands, and invoke skills.
Each block receives a fresh prompt built from trusted Natural text, Python values, inherited working state, and scoped configuration.
Shared objects can carry mutations between calls; model transcripts are not inherited.

## Design landscape

Nighthawk sits between two common approaches: orchestration frameworks and literate-programming-style harnesses.

### Orchestration frameworks

Frameworks such as LangGraph, CrewAI, and AutoGen put orchestration into a graph or agent runtime. The graph runtime executes developer-defined routing; in agentic configurations, the LLM may participate in tool choice or routing. Contracts are enforced through framework-managed schema, guardrails, routing conditions, and graph state.

This is a good fit when multi-agent coordination is the core product, when conversation history is central, or when the application wants a dedicated graph runtime.

### Literate-programming-style harnesses

Agent skills, instruction files, and similar systems express orchestration in natural language with embedded code snippets for strict procedures. They are useful for teaching an agent how to operate inside a project, but their state boundaries are usually conventional rather than typed.

A prompt can ask an agent to store a result, but the surrounding application still needs an explicit contract for receiving and validating it.
Nighthawk expresses that transfer with typed output bindings.

### Nighthawk

Nighthawk keeps orchestration in Python. State lives in Python variables and crosses block boundaries through bindings. Contracts are expressed through Python types and enforced through runtime validation, structured outcomes, deny frontmatter, and explicit block boundaries.

| | Orchestration frameworks | Literate harnesses | Nighthawk |
|---|---|---|---|
| Control | Graph or agent runtime | Natural language instructions | Python control flow |
| State | Graph state, message history | Prompt narrative | Python values and declared output bindings |
| Cross-step context | Often implicit | Often implicit | Python values, inherited working state, scoped task injection |
| Debugging | Framework-specific tooling | Prompt inspection | Python debugger, pytest, tracing |
| Constraint model | Guardrails, schemas, routing conditions | Natural language conventions | Type validation, deny frontmatter, structured outcomes, scoped oversight |

Contextual inheritance reduces argument plumbing but makes some dependencies implicit.
Prefer explicit parameters for essential inputs and inheritance for intentionally shared task policy.
Joined children inherit parent associations; mutable objects retain their shared identity.

## Design consequences

Reliability depends on the contract around a judgment as well as the model and prompt: what context is available, what outputs are accepted, and how failures recover.
Whether a particular design works is an empirical question for the application's [tests](verification.md).

### Resilience as composable functions

Retry, fallback, voting, timeout, budget, and circuit-breaker policies compose as ordinary Python function transformers.
The host chooses the retry boundary, exception handling, logging, and recovery policy.
See [Patterns](patterns.md#resilience-patterns) for examples.

### Scoped execution contexts

Configuration follows Python control flow through context variables.
Hosts can admit whole function calls, select child models through preserving executor wrappers, and observe block or request outcomes at the appropriate boundary.
Core restores context and joins owned work; the application owns approval, accounting, and recovery.
See [Runtime configuration](runtime-configuration.md).

### Tool exposure efficiency

Python-local helpers need only a signature in the prompt and execute in the host process.
MCP suits cross-process or cross-language integrations; CLI tools suit capabilities already exposed as commands.
Choose the interface that matches the operation and its validation needs.

| Interface | Best fit | Composition | Boundary |
|---|---|---|---|
| Binding function | Python-local helper | Python values and calls | Python execution with validated writeback |
| MCP | Cross-process capability | Protocol messages | Tool schemas and backend policy |
| CLI | Existing command-line tool | Shell arguments and output | Process I/O and operating-system permissions |

### Multi-agent coordination without a framework

Natural functions compose with ordinary Python concurrency, queues, or service calls.
The host coordinates communication and merging; each block has its own working assignments.
Shared mutations require application coordination, while filesystem and process isolation belong to the host or backend.
See the [carry pattern](patterns.md#the-carry-pattern) for deliberate shared state.

## Trust and safety boundaries

Natural source, imported markdown, and prompt fragments define executable program structure and must be trusted, repository-managed assets.
Pass user input as binding values rather than interpolating it into that structure.

Typed bindings validate name writeback; they do not sandbox arbitrary instructions or make a block transactional.
Helper calls, object mutations, validators, and external operations can have effects before finalization.
The host owns approval, durable records, compensation, and retry decisions.

Core waits for its owned work, including cancellation cleanup, before reporting completion.
That wait may be indefinite; it neither rolls back effects nor joins detached application work.
Coding-agent permissions and operating-system isolation govern what the backend may do during execution.

## Tradeoffs

The boundary-centric design has real costs:

- **Python lock-in.** Binding functions, type annotations, scopes, and resilience transformers are Python constructs. Nighthawk does not offer a language-neutral orchestration protocol.
- **Per-invocation cost.** Every Natural block invocation calls an LLM or launches an agent execution. Deterministic work should stay in Python.
- **Manual orchestration burden.** Branching, retry policy, merge logic, recovery, and cancellation remain user-code responsibilities rather than graph-runtime responsibilities.
- **Integration tests are essential.** Mock tests verify Python logic around Natural blocks. Real LLM behavior requires integration tests against the provider and model you will use. See [Verification](verification.md).
- **API design discipline.** Binding functions are only as useful as their names, signatures, type annotations, and docstring intent.
- **Backend-dependent isolation.** Nighthawk validates what crosses back into Python, but backend sandboxes control what an autonomous agent may do while executing.

## Why evaluate every time

Interpretation and judgment depend on the particular input and context.
Compiling a Natural block once into deterministic Python would freeze those decisions and still require validation of the generated logic.
If explicit rules can determine the answer, write Python directly.

Per-invocation evaluation lets a model or coding agent respond to the current data and environment.
Nighthawk constrains that work with typed outputs and Python-owned control flow; [verification](verification.md) establishes whether the resulting judgments meet the application's needs.
