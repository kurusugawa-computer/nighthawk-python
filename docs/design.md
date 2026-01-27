# Nighthawk design

This document is the specification for Nighthawk.

## 0. Document scope

This document specifies:

- What counts as a Natural block (docstring and inline).
- The Natural DSL binding syntax (`<name>`, `<:name>`).
- The execution model (state layers, tools, and the final JSON contract).
- The host-facing environment API and configuration surface.

This document does not attempt to describe every compilation or implementation detail. The current implementation in `src/nighthawk/` is expected to match this specification.

## 0.1 Alignment policy

Nighthawk treats this document as the target behavior. If you find a mismatch between this document and the implementation:

- Prefer changing the implementation to match this document.
- If the document is wrong or outdated, update the document and adjust tests so the spec and implementation remain aligned.

This file intentionally does not maintain a persistent divergence ledger.

## 1. Goals

- Provide a compact reimplementation of nightjarpy-like Natural blocks in Python.
- Support a hybrid style where Python controls flow, while the LLM executes a Natural DSL embedded as:
  - Function docstring Natural blocks
  - Inline Natural blocks (standalone string literal statements)
- Reduce the "LLM is a black box" problem by actively mapping LLM-relevant state into the Python interpreter:
  - expose a summary of execution locals to the LLM
  - allow the LLM to synchronize intermediate state into an execution locals mapping during reasoning
  - commit selected state back into Python locals at Natural block boundaries
- Map state into a user-defined Pydantic `BaseModel` ("memory") for recognition alignment and validation.

## 2. Non-goals

- Sandboxing or hard security isolation.
- Persistence across processes.
- A full "Skills" framework.
- Executing Python code blocks embedded in markdown (a broader "Natural -> Python -> Natural" nesting beyond docstrings).

## 3. Hard constraints

- Python 3.14+ (template preprocessing uses Python 3.14 template strings).
- Default model: `openai:gpt-5-nano`.
- Recommended model for quality: `openai:gpt-5.2`.
- LLM provider: OpenAI only, integrated via `pydantic-ai-slim[openai]`.
- Threat model: Natural blocks and imported markdown are trusted and repository-managed.

## 4. Terminology

- Natural block: a Python docstring or inline string literal beginning with the sentinel `natural`.
- Natural DSL: the constrained syntax inside a Natural block (token binding plus free-form instructions).
- Python locals (`python_locals`): the actual Python local variables in the function's frame.
- Python globals (`python_globals`): the Python module globals for the compiled function.
- Execution locals (`execution_locals`): a locals mapping used as the execution environment for LLM expressions; updated during reasoning via tools.
- Execution globals (`execution_globals`): a limited globals mapping used as the execution environment for LLM expressions.
- Locals summary: a bounded text rendering of selected values from `execution_locals`, included in the LLM prompt.
- Memory: a structured state model (Pydantic `BaseModel`) stored and validated by the host.
- Control-flow effect: a request to the Python interpreter to run `continue`, `break`, or `return`.

## 5. User-facing API

### 5.1. Decorator

- `nighthawk.fn`
  - Decorator that compiles a function containing Natural blocks into an LLM-backed implementation.
  - Compilation happens at decoration time, and Natural blocks are executed at function call time.
  - Note: The decorator requires the function source to be available for inspection.

### 5.2. Configuration

- `Configuration`
  - `execution_configuration`: configuration for execution.

- `ExecutionConfiguration`
  - `model`: Model identifier in `provider:model` format. Default: `openai:gpt-5-nano`.
    - Examples: `openai:gpt-5.2`, `openai:gpt-5-nano`.
  - `tokenizer_encoding`: tokenizer encoding identifier for approximate token budgeting. Default: `o200k_base`.
  - `prompts`: prompt templates used for execution.
    - `execution_system_prompt_template`: system prompt template that defines the execution protocol.
    - `execution_user_prompt_template`: full user prompt template including section delimiters.
  - `context_limits`: limits for rendering dynamic context into the prompt.
    - v1 uses an approximate conversion `max_chars = max_tokens * 4`.
  - `context_redaction`: rules for reducing or masking sensitive data in prompt context.

`context_redaction` minimal requirements:

- Allowlist behavior:
  - Locals allowlist: if empty, all local names are eligible for inclusion; if non-empty, only listed names are eligible.
  - Memory fields allowlist: if empty, all memory field names are eligible; if non-empty, only listed field names are eligible.
- Masking behavior:
  - If a local name or memory field name matches a configured mask rule (for example a substring match), its value is replaced with a fixed marker.
- v1 limitation:
  - Redaction is shallow: it applies to which top-level names are shown and whether their values are replaced by a marker. It is not a recursive structured scrubber.

## 6. Natural block detection

Nighthawk recognizes Natural blocks in two places.

1) Function docstring Natural block

A function is considered a Natural function when it has a docstring whose underlying string literal begins with:

- `natural\n`

The remainder of the docstring is the Natural program.

Recommended form:

- `"""natural\n..."""`

2) Inline Natural blocks

Inside a function body, a standalone string literal expression statement whose underlying string literal begins with:

- `natural\n`

Decision (inline shape):

- The inline Natural block is defined by the AST shape "expression statement containing a string literal".
- Parentheses do not matter.

Sentinel rules (both docstring and inline):

- The sentinel is case-sensitive and must match exactly `natural`.
- The literal must begin with `natural\n` (no leading blank lines, no leading whitespace).
- The sentinel line must contain only `natural` (no trailing whitespace).

## 7. Natural DSL: bindings

The Natural program may contain bindings with angle brackets:

- `<name>`: input binding. The current Python value of `name` is made available to the LLM.
- `<:name>`: writable binding. The LLM may update the value of `name`.

Constraints:

- `name` is a simple identifier (no dotted paths).
- `<:name>` does not require prior declaration.
  - Practical note: if subsequent Python code reads a variable that has not been assigned yet, Python will raise before any LLM behavior can help. Initialize variables in Python when needed.

Type note:

- Nighthawk extracts type information for `<:name>` bindings from the function source AST at compile time.
- If no type annotation is found, the type is treated as `Any`.

Clarifying note (bindings vs tool targets):

- Bindings (`<name>`, `<:name>`) are always simple identifiers.
- Tool targets (for example `nh_assign`) may use dotted paths for attribute mutation.
- Commit selection remains based on `<:name>` identifiers (top-level names only).

## 8. Orchestrator model

### 8.1. State layers: python locals, execution locals, memory

Nighthawk uses multiple state layers.

1) Python locals (`python_locals`)

- These are the actual local variables in the executing Python function.
- After a Natural block finishes, selected values are committed into Python locals so subsequent Python code can read them.

2) Execution locals (`execution_locals`)

- `execution_locals` is a mapping used as the locals environment for LLM expression evaluation.
- It is initialized at the start of each Natural block execution:
  - If nested execution exists, start from the outer execution's `execution_locals` values.
  - Overlay the caller frame's current `python_locals`.
  - Bind `memory` into `execution_locals`.
- During execution, the LLM can update `execution_locals` via tools (Section 8.3).
- At the end of execution, values for `<:name>` bindings are committed into Python locals.

3) Memory

- A user-defined Pydantic `BaseModel` used for recognition alignment.
- The host owns the memory instance for the duration of the environment.
- The LLM may update memory during reasoning via tools (Section 8.3).

### 8.2. Prompt context: locals summary and memory summary

To reduce black-box behavior, Nighthawk includes bounded prompt context sections.

Locals summary:

- The prompt includes a rendered view of selected names from `execution_locals`.
- Rendering is bounded by `context_limits` (approximate token budgeting) and may truncate.
- Rendering applies `context_redaction` allowlists and masking rules.

Memory summary:

- The prompt includes a JSON rendering of the current memory state.
- Rendering is bounded by `context_limits` (approximate token budgeting) and may truncate.
- Rendering applies `context_redaction` allowlists and masking rules.

### 8.3. Tools available to the LLM

Tools are Python callables exposed to the LLM via pydantic-ai tool calling.

User-defined tools:

- The host defines tools using the `@nighthawk.tool` decorator.

Provided tools (built-in):

- Provided tools are always available by default.
- Provided tools are exposed with names prefixed by `nh_` to reduce collisions.

Tools operate against `execution_locals` and `execution_globals`.

Decision (execution_globals):

- `execution_globals` includes only `__builtins__`.

The host binds `memory` into `execution_locals`, so expressions can read the current memory state.

Read tools:

- `nh_dir(expression: str) -> str`
- `nh_help(expression: str) -> str`
- `nh_eval(expression: str) -> str`
  - Evaluates a Python expression in `execution_globals` and `execution_locals`.
  - Returns JSON text.

Write tool:

- `nh_assign(target: str, expression: str) -> object`

Target grammar:

- `target := name ("." field)*`
- `name` and `field` are ASCII Python identifiers.

Reserved targets:

- Assigning to the root name `memory` is disallowed.
  - `memory.<field>` (and deeper dotted paths) are allowed.
- Any segment starting with `__` (dunder) is disallowed.

Semantics of `nh_assign`:

- Evaluate `expression` as a Python expression using `execution_globals` and `execution_locals`.
- If `target` is a bare `name`:
  - Assign into `execution_locals[name]`.
  - Validation:
    - If extracted type information is available for the corresponding `<:name>` binding, validate/coerce to that type.
    - Otherwise, assign without validation.
- If `target` is dotted (`name.field...`):
  - Resolve the root object from `execution_locals[name]`.
  - Traverse attributes for each intermediate segment.
  - Assign using attribute assignment on the final segment.
  - Validation:
    - Validate only when runtime type metadata is available; otherwise assign without validation.

Commit and mutation notes:

- Commit selection is controlled only by `<:name>` bindings.
- `<:name>` selects which top-level names are committed from `execution_locals` into Python locals at Natural block boundaries.
- Dotted mutation is independent of `<:name>`.

Write tool return value:

- The tool returns a diagnostic object describing:
  - whether it succeeded
  - a bounded summary of the updated value (on success)
  - validation details (when relevant)
  - error details (on failure)

Atomicity requirement:

- `nh_assign` is atomic: if traversal, evaluation, or validation fails, it performs no updates.

### 8.4. Execution contract (final JSON)

At the end of each execution, the LLM returns a final JSON object.

- `effect`: optional object
  - Control-flow effect requested by the Natural block.
  - Keys:
    - `type`: string, one of `continue`, `break`, `return`
    - `value_json`: optional string
      - If `type` is `return`, this may be provided as a JSON text representing the function return value.
      - The host parses `value_json` using `json.loads` and validates/coerces the resulting Python value to the function's return type annotation.
      - If `value_json` is omitted or `null`, the return value is treated as `None`.

If execution fails, the LLM returns:

- `error`: object
  - `message`: string
  - `type`: string (optional)

The implementation chooses strict parsing. Any non-JSON final response is an error.

Notes:

- Control-flow effects are expressed only via the final JSON `effect`.
- `break` and `continue` effects are valid only when the Natural block appears syntactically inside a Python `for` or `while` loop. If requested outside a loop, execution fails.
- Python locals are committed at Natural block boundaries based on `<:name>` bindings.
- Memory is updated via tools during reasoning and is not returned in the final JSON.

### 8.5. Stub backend contract

In stub mode, Nighthawk does not call an LLM. Instead, it reads an execution envelope from the Natural program text.

- Parsing rule: inside the Natural block text, stub mode finds the first `{` character and parses the substring starting there as a JSON object.
- The JSON object must be an envelope with:
  - `execution_final`: an object matching the ExecutionFinal schema
  - `bindings`: an object mapping names to values
- Stub mode returns `execution_final` from the envelope.
- Stub mode returns a bindings object filtered to include only names present in the `<:name>` binding list for that Natural block.

## 9. Return value

In the simplest docstring pattern, the Python function body returns a variable that is updated by execution:

- `return result`

If an execution requests `effect.type == "return"`, the orchestrator returns the validated return value immediately.

## 10. Environment

Nighthawk uses an implicit environment (dynamic scoping) carried via `contextvars.ContextVar`.

The environment is required for execution and contains:

- `execution_configuration` (required): execution configuration.
- `workspace_root` (required): base directory for include resolution.
- `execution_executor` (required): a runner strategy object responsible for executing Natural blocks.
- `memory` (required): a Pydantic `BaseModel` instance owned by the host for the duration of the environment.

API:

- `nighthawk.environment(environment: ExecutionEnvironment)`
  - Replace/bootstrap. Can be used even when no environment is currently set.
- `nighthawk.environment_override(...)`
  - Overlay. Requires an existing environment. Only specified fields are overridden for the duration of the `with`.
- `nighthawk.get_environment() -> ExecutionEnvironment`
  - Get the current environment. Raises if unset.

## 11. Template preprocessing (runtime)

### 11.1. Rationale

Natural programs may reference external markdown or compose prompts. Nighthawk supports this by preprocessing the Natural block at runtime.

### 11.2. Mechanism

- The Natural block is evaluated as a Python 3.14 template string at runtime (function call time).
- The template evaluation environment is the caller frame's Python environment:
  - `python_locals`: the caller frame locals.
  - `python_globals`: the caller frame globals.
  - Name resolution follows Python rules (locals shadow globals).
- Nighthawk does not provide built-in template helper functions.
  - If hosts want helpers (for example `include(path)`), they should bind them into the caller frame locals or globals.

Note:

- Template preprocessing is distinct from the `nh_eval` tool. Template evaluation uses the caller frame environment, while the `nh_eval` tool uses `execution_globals` and `execution_locals`.

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

- This is an example, not a fixed requirement.

## 13. Error handling

Nighthawk distinguishes:

- Parse errors: invalid or non-JSON LLM output.
- Tool evaluation errors: invalid expressions, runtime errors from expression evaluation.
- Tool validation errors: type validation/coercion fails for `nh_assign`.

All errors are surfaced as Python exceptions.
