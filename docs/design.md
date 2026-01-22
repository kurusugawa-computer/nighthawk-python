# Nighthawk design

This document describes the current design and implementation constraints for Nighthawk.

## Alignment notes (2026-01-22)

This document currently includes both implemented behavior and intended near-term behavior. When the implementation and this document diverge, we explicitly choose which side is authoritative and record it here.

Decisions for the next implementation steps:

- Configuration: adopt the current implementation shape (minimal Configuration) and move expansion items to docs/roadmap.md.
- Tool eval return shape: adopt the current implementation (eval returns JSON text).
- Template preprocessing: design target is to evaluate templates using the caller frame's Python locals and globals. Nighthawk does not provide built-in template helpers; hosts can bind functions (for example include) into the caller frame environment.
- Runtime context: agent and memory are required by the runtime context API.
- Decorator: keep design at a high level; avoid describing compilation mechanics in detail.

Known gaps (implementation differs from this document as of 2026-01-22):

- Locals summary and memory summary are specified (Section 8.2) but not yet implemented.
- The assign tool's diagnostic return object is specified (Section 8.3) but the current implementation returns a reduced shape.
- Compile-time type extraction for `<:name>` bindings is specified (Section 7) but not yet connected end-to-end.
- The final JSON contract and control-flow effects are specified (Section 8.4 and Section 9) but are not yet implemented end-to-end.
- The runtime state-layer contract is specified (Section 8.1), but the current implementation does not yet fully match the commit and prompt-context behavior described here.

## 1. Goals

- Provide a compact reimplementation of nightjarpy-like Natural blocks in Python.
- Support a hybrid style where Python controls flow, while the LLM executes a Natural DSL embedded as:
  - Function docstring Natural blocks
  - Inline Natural blocks (standalone string literal statements)
- Reduce the "LLM is a black box" problem by actively mapping LLM-relevant state into the Python interpreter:
  - expose a summary of local variables to the LLM
  - allow the LLM to synchronize intermediate state into a context locals mapping during reasoning
  - commit selected state back into Python locals at Natural block boundaries
- Map state into a user-defined Pydantic `BaseModel` ("memory") for recognition alignment and validation.

## 2. Non-goals

- Sandboxing or hard security isolation.
- Persistence across processes.
- A full "Skills" framework.
- Executing Python code blocks embedded in markdown (a broader "Natural -> Python -> Natural" nesting beyond docstrings).

## 3. Hard constraints

- Python 3.14+ (template preprocessing uses Python 3.14 template strings).
- Recommended OpenAI model: `gpt-5.2`.
- LLM provider: OpenAI only, integrated via `pydantic-ai-slim[openai]`.
- Threat model: Natural blocks and imported markdown are trusted and repository-managed.

## 4. Terminology

- Natural block: a Python docstring or inline string literal beginning with the sentinel `natural`.
- Natural DSL: the constrained syntax inside a Natural block (token binding plus free-form instructions).
- Python locals (`python_locals`): the actual Python local variables in the function's frame.
- Python globals (`python_globals`): the Python module globals for the compiled function.
- Context locals (`context_locals`): a locals mapping used as the evaluation environment for LLM expressions; updated during reasoning via tools.
- Context globals (`context_globals`): a limited globals mapping used as the evaluation environment for LLM expressions.
- Locals summary: a bounded text summary of (selected) locals across the call stack, included in the LLM prompt.
- Memory: a structured state model (Pydantic `BaseModel`) stored and validated by the host; updated during reasoning via tools.
- Control-flow effect: a request to the Python interpreter to run `continue`, `break`, or `return`.

## 5. User-facing API

### 5.1. Decorator

- `nighthawk.fn`
  - Decorator that compiles a function containing Natural blocks into an LLM-backed implementation.
  - Compilation happens at decoration time, and Natural blocks are executed at function call time.
  - Note: The decorator requires the function source to be available for inspection.

### 5.2. Configuration

- `Configuration`
  - OpenAI model name.

Notes:

- Nighthawk intentionally keeps Configuration minimal in the current implementation.
- Configuration expansion ideas (environment variable support, tool enablement flags, template context controls, locals/memory summary options) are tracked in docs/roadmap.md.

## 6. Natural block detection

Nighthawk recognizes Natural blocks in two places.

1) Function docstring Natural block

A function is considered a Natural function when it has a docstring whose first non-empty line is exactly:

- `natural`

The remainder of the docstring is the Natural program.

2) Inline Natural blocks

Inside a function body, a standalone string literal expression statement whose first non-empty line is exactly `natural` is treated as a Natural block.

Decision (inline shape):

- The inline Natural block is defined by the AST shape "expression statement containing a string literal".
- Parentheses do not matter. For example, `"""natural\n..."""` and `("""natural\n...""")` are treated the same.

Notes:

- The sentinel is case-sensitive and must match exactly `natural`.
- Sentinel matching rule: after docstring/inline string literal unwrapping, skip leading empty lines; the first logical line must be exactly `natural` (no leading or trailing whitespace).

## 7. Natural DSL: bindings

The Natural program may contain bindings with angle brackets:

- `<name>`: input binding. The current Python value of `name` is made available to the LLM.
- `<:name>`: output binding. The LLM may update the value of `name`.

Constraints:

- `name` is a simple identifier (no dotted paths).
- For `<:name>`, the variable is expected to be declared in Python before the Natural block.

Type note:

- Local variable annotations are not generally available at runtime. Nighthawk is expected to extract type information for `<:name>` bindings from the function source AST at compile time.
- If no type annotation is found, the type is treated as `Any`.

## 8. Runtime model

### 8.1. State layers: python locals, context locals, memory

Nighthawk uses multiple state layers.

1) Python locals (`python_locals`)

- These are the actual local variables in the executing Python function.
- After a Natural block finishes, selected values are committed into Python locals so subsequent Python code can read them.

2) Context locals (`context_locals`)

- `context_locals` is a mapping used as the locals environment for expression evaluation.
- It is initialized from the current Python locals at the start of each Natural execution.
- During a Natural execution, the LLM can update `context_locals` via tools (Section 8.3).
- At the end of the Natural execution, values for `<:name>` bindings are committed from `context_locals[name]` into Python locals.

3) Memory

- A user-defined Pydantic `BaseModel` used for recognition alignment.
- The host owns and persists the current memory instance for the duration of the process.
- The LLM may update memory during reasoning via tools (Section 8.3).
- Each memory update is validated (and may be coerced) atomically.

### 8.2. Locals summary and memory summary (prompt context)

To reduce black-box behavior, Nighthawk includes a locals summary in the prompt.

Locals summary:

- The summary may walk up the call stack.
- The summary is built by concatenating per-frame summaries until a maximum total length is reached (for example 10000 characters).

Recommended summarization rules:

- For `str`, include a sliced value.
- For `int`, `float`, `bool`, and `None`, include the immediate value.
- For containers, include shape summaries (for example length and a small sample of keys/elements).
- For other objects, include the type name and a short, bounded representation.

Memory summary:

- The host provides a bounded summary of the current memory state in the prompt.
- The memory schema influences the LLM's mental model and may be iterated over time.

### 8.3. Tools available to the LLM

Tools operate against `context_locals` and a limited `context_globals`.

Decision (context_globals):

- `context_globals` includes only `__builtins__` (no additional helpers).

The host pre-binds `memory` into `context_locals`, so expressions can read the current memory state.

Read tools:

- `dir(expr: str) -> str`
- `help(expr: str) -> str`
- `eval(expr: str) -> str`
  - Evaluates a Python expression in `context_globals` and `context_locals`.
  - Returns JSON text (for example via `json.dumps(obj, default=repr)`).

Write tool:

- `assign(target: str, expression: str) -> object`

Target grammar:

- Local target: `<name>`
  - `<name>` must be a simple identifier.
  - `<name>` is restricted to the allowlist derived from `<:name>` bindings in the current Natural block.

- Memory target: `memory.<field>`
  - `<field>` must be a top-level memory field name (a simple identifier).
  - Memory targets are available only if memory is enabled.

Semantics of `assign`:

- The tool evaluates `expression` as a Python expression using `eval(expression, context_globals, context_locals)`.
- If `target` is a local target `<name>`:
  - The tool validates and may coerce the resulting value to the declared type of `<name>` (Pydantic-based validation).
  - If validation succeeds, it updates `context_locals[<name>]`.
- If `target` is a memory target `memory.<field>`:
  - The tool validates and may coerce the resulting value to the declared type of that memory field (Pydantic-based validation).
  - If validation succeeds, it updates that memory field.
- If evaluation fails or validation fails, it performs no updates (atomic failure).

Write tool return value:

The tool returns a diagnostic object (JSON) describing:

- whether it succeeded
- a short summary of the updated value
- validation details
- error details if it failed

### 8.4. Natural execution contract (final JSON)

At the end of each Natural execution, the LLM returns a final JSON object.

- `effect`: optional object
  - Control-flow effect requested by the Natural block.
  - Keys:
    - `type`: string, one of `continue`, `break`, `return`
    - `value_json`: optional string
      - If `type` is `return`, this may be provided as a JSON text representing the function return value.
      - The host validates/coerces the return value to the function's return type annotation (or treats it as `Any` if unspecified).

If execution fails, the LLM returns:

- `error`: object
  - `message`: string
  - `type`: string (optional)

The implementation chooses strict parsing. Any non-JSON final response is an error.

Notes:

- Control-flow effects are expressed only via the final JSON `effect` (there are no control-flow effect tools).
- Python locals are committed from `context_locals` at Natural block boundaries based on `<:name>` bindings.
- Memory is updated via tools during reasoning and is not returned in the final JSON.

## 9. Return value

In the simplest docstring pattern, the Python function body returns a variable that is updated by the Natural execution:

- `return result`

The host commits `context_locals["result"]` into the Python local `result` at the end of the Natural execution.

If a Natural execution requests `effect.type == "return"`, the runtime returns the validated return value immediately.

## 10. Runtime context

Nighthawk uses an implicit runtime context (dynamic scoping) carried via `contextvars.ContextVar`.

The runtime context is required for Natural execution and contains:

- `configuration` (required)
- `workspace_root` (required): base directory for include resolution.
- `agent` (required): the LLM execution agent.
- `memory` (required): a Pydantic `BaseModel` instance owned by the host for the duration of the context.

API:

- `nighthawk.runtime_context(ctx: RuntimeContext)`
  - Replace/bootstrap. Can be used even when no context is currently set.
- `nighthawk.runtime_context_override(...)`
  - Overlay. Requires an existing context. Only specified fields are overridden for the duration of the `with`.
- `nighthawk.get_runtime_context() -> RuntimeContext`
  - Get the current context. Raises if unset.

Example:

```py
import nighthawk as nh
from pydantic import BaseModel

cfg = nh.Configuration(model="gpt-5.2")

class Memory(BaseModel):
    pass

memory = Memory()
agent = ...

with nh.runtime_context(
    nh.RuntimeContext(
        configuration=cfg,
        workspace_root="/path/to/workspace",
        agent=agent,
        memory=memory,
    )
):
    ...

with nh.runtime_context_override(workspace_root="/tmp/repo"):
    ...
```

## 11. Template preprocessing (runtime)

### 11.1. Rationale

Natural programs may reference external markdown or compose prompts. Nighthawk supports this by preprocessing the Natural block at runtime.

### 11.2. Mechanism

- The Natural block is evaluated as a Python 3.14 template string at runtime (function call time).
- The template evaluation environment is the caller frame's Python environment:
  - `python_locals`: the caller frame locals.
  - `python_globals`: the caller frame globals (so imported functions and module-level symbols are available).
  - Name resolution follows Python rules (locals shadow globals).
- Nighthawk does not provide built-in template helper functions.
  - If hosts want helpers (for example `include(path)`), they should bind them into the caller frame locals or globals.

Note:

- Template preprocessing is distinct from the `eval` tool. Template evaluation uses the caller frame environment, while the `eval` tool uses `context_globals` and `context_locals`.

Decision:

- Template preprocessing may execute arbitrary functions.
- This is acceptable only under the trusted-input threat model.

## 12. Memory model (example shape)

The user defines a `MemoryModel` as a Pydantic `BaseModel` using shallow types.

Example fields:

- `facts: dict[str, str] = {}`
- `glossary: dict[str, str] = {}`
- `decisions: list[Decision] = []`
- `examples: list[Example] = []`
- `notes: list[str] = []`

Where `Decision` and `Example` are also shallow `BaseModel` types.

Note:

- This is an example, not a fixed requirement. The memory schema influences the LLM's mental model.

## 13. Error handling

Nighthawk distinguishes:

- Parse errors: invalid or non-JSON LLM output.
- Tool evaluation errors: invalid expressions, runtime errors from expression evaluation.
- Tool validation errors: type validation/coercion fails for `assign`.

All errors are surfaced as Python exceptions.
