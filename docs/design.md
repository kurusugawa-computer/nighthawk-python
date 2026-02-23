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
  - expose a summary of step locals to the LLM
  - allow the LLM to synchronize intermediate state into a step locals mapping during reasoning
  - commit selected state back into Python locals at Natural block boundaries
- Provide a coherent execution model where all state is ordinary Python values in step locals, and persistence (if desired) is user-managed via ordinary bindings.

## 2. Non-goals

- Sandboxing or hard security isolation.
- Persistence across processes.
- A full "Skills" framework.
- Executing Python code blocks embedded in markdown (a broader "Natural -> Python -> Natural" nesting beyond docstrings).

## 3. Hard constraints

- Python 3.13+.
- Default model: `openai-responses:gpt-5-nano`.
- Recommended model for quality: `openai-responses:gpt-5.2`.
- Optional backends are installed via extras: `openai`, `vertexai`, `claude-code`, `codex`.
- Threat model: Natural blocks and imported markdown are trusted and repository-managed.

## 4. Terminology

- Natural block: a Python docstring or inline string literal beginning with the sentinel `natural`.
- Natural DSL: the constrained syntax inside a Natural block (token binding plus free-form instructions).
- Python locals (`python_locals`): the actual Python local variables in the function's frame.
- Python globals (`python_globals`): the Python module globals for the compiled function.
- Step locals (`step_locals`): a locals mapping used as the execution environment for LLM expressions; updated during reasoning via tools.
- Step globals (`step_globals`): a limited globals mapping used as the execution environment for LLM expressions.
- StepContext: a mutable, per-step object (one Natural block execution) passed to tools and executors.
  - Required fields include `step_id` (unique Id for the step) and `run_configuration`.
  - Model selection is sourced only from `run_configuration.model`; StepContext does not carry a separate `model` field.
- Locals summary: a bounded text rendering of selected values from `step_locals`, included in the LLM prompt.
- Prompt suffix fragment: additional prompt text appended to the end of the effective system prompt or user prompt for the duration of a scoped override.
- Outcome: the single, unambiguous result of executing a Natural block.
- Outcome kind: the required `kind` field on an outcome object. The baseline kinds are `pass`, `return`, `break`, `continue`, and `raise`.
- Allowed outcome set: the set of outcome types allowed for a specific Natural block instance, derived from syntactic context and deny-only frontmatter.
- Frontmatter: optional YAML metadata at the start of a Natural program, delimited by `---` lines.

## 5. User-facing API

### 5.1. Decorator

- `nighthawk.natural_function`
  - Decorator that compiles a function containing Natural blocks into an LLM-backed implementation.
  - Compilation happens at decoration time, and Natural blocks are executed at function call time.
  - Note: The decorator requires the function source to be available for inspection.

### 5.2. Configuration

- `NighthawkConfiguration`
  - `run_configuration`: configuration for execution.

- `RunConfiguration`
  - `model`: Model identifier in `provider:model` format. Default: `openai-responses:gpt-5-nano`.
    - Examples: `openai-responses:gpt-5.2`, `openai-responses:gpt-5-nano`.
    - Special cases:
      - `claude-code:default` and `codex:default` select the backend/provider default model (no explicit model selection is sent to the backend).
  - `tokenizer_encoding`: tokenizer encoding identifier for approximate token budgeting. Default: `o200k_base`.
  - `prompts`: prompt templates used for execution.
    - `step_system_prompt_template`: system prompt template that defines the step execution protocol.
    - `step_user_prompt_template`: full user prompt template including section delimiters.
  - `context_limits`: limits for rendering dynamic context into the prompt.
  - `context_redaction`: rules for reducing or masking sensitive data in prompt context.

`context_redaction` minimal requirements:

- Allowlist behavior:
  - Locals allowlist: if empty, all local names are eligible for inclusion; if non-empty, only listed names are eligible.
- Masking behavior:
  - If a local name matches a configured mask rule (for example a substring match), its value is replaced with a fixed marker.
  - The default marker is `[redacted]`.
- v1 limitation:
  - Redaction is shallow: it applies to which top-level names are shown and whether their values are replaced by a marker. It is not a recursive structured scrubber.

## 6. Natural block detection

Nighthawk recognizes Natural blocks in two places.

1) Function docstring Natural block

A function is considered a Natural function when it has a docstring whose underlying string literal begins with:

- `natural\n`

The Natural program is derived from the docstring by:

1) Removing the leading `natural\n` sentinel prefix.
2) Normalizing indentation by applying `textwrap.dedent` to the remainder.

This normalization exists because Natural blocks are typically indented inside Python code. It ensures the Natural program text is stable regardless of surrounding Python indentation.

Recommended form:

- `"""natural\n..."""`

2) Inline Natural blocks

Inside a function body, a standalone expression statement whose AST is syntactically a string literal begins with:

- `natural\n`

The expression value must be either:

- A plain string literal, or
- An f-string literal

The Natural program is derived at runtime using the same rules as a docstring Natural block: remove the `natural\n` prefix, then apply `textwrap.dedent` to the remainder.

Docstring note:

- A docstring Natural block is always a plain string literal.
- Even if an f-string is the first statement in a function body, it is not a docstring, and it is treated as an inline Natural block.

Decision (inline shape):

- The inline Natural block is defined by the AST shape "expression statement containing a string literal (including f-strings)".
- Parentheses do not matter.

Sentinel rules (both docstring and inline):

- The sentinel is case-sensitive and must match exactly `natural`.
- The literal must begin with `natural\n` (no leading blank lines, no leading whitespace).
- The sentinel line must contain only `natural` (no trailing whitespace).

## 7. Natural DSL: bindings

The Natural program may contain bindings with angle brackets:

- `<name>`: input binding. The current Python value of `name` is made available to the LLM.
- `<:name>`: writable binding. The LLM may update the value of `name`.

Resolution note:

- Input binding reads resolve names using Python lexical rules (LEGB: locals, enclosing, globals, builtins).
- If a name is missing or unbound, the error is surfaced as a Python exception type where feasible (for example `NameError`, `UnboundLocalError`).

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

## 8. Runner model

### 8.1. State layers: python locals and step locals

Nighthawk uses multiple state layers.

1) Python locals (`python_locals`)

- These are the actual local variables in the executing Python function.
- After a Natural block finishes, selected values are committed into Python locals so subsequent Python code can read them.

2) Step locals (`step_locals`)

- `step_locals` is a mapping used as the locals environment for LLM expression evaluation.
- It is initialized at the start of each Natural block execution:
  - If nested execution exists, start from the outer execution's `step_locals` values.
  - Overlay the caller frame's current `python_locals`.
- During execution, the LLM can update `step_locals` via tools (Section 8.3).
- At the end of execution, values for `<:name>` bindings are committed into Python locals.


### 8.2. Prompt context: locals summary

To reduce black-box behavior, Nighthawk includes bounded prompt context sections.

Locals summary:

- The prompt includes a rendered view of selected names from `step_locals`.
- Rendering is bounded by `context_limits` (approximate token budgeting) and may truncate.
- Rendering applies `context_redaction` allowlists and masking rules.


### 8.3. Tools available to the LLM

Tools are Python callables exposed to the LLM via pydantic-ai tool calling.

User-defined tools:

- The host defines tools using the `@nighthawk.tool` decorator.

Provided tools (built-in):

- Provided tools are always available by default.
- Provided tools are exposed with names prefixed by `nh_` to reduce collisions.

Tools operate against `step_locals` and `step_globals`.

Decision (step_globals):

- `step_globals` includes only `__builtins__`.

Expressions are evaluated against `step_globals` + `step_locals`.

Read tools:

- `nh_dir(expression: str) -> str`
- `nh_help(expression: str) -> str`
- `nh_eval(expression: str) -> str`
  - Evaluates a Python expression in `step_globals` and `step_locals`.
  - Returns JSON text.

Write tool:

- `nh_assign(target_path: str, expression: str) -> object`

Target grammar:

- `target_path := name ("." field)*`
- `name` and `field` are ASCII Python identifiers.

Reserved targets:

- Any segment starting with `__` (dunder) is disallowed.

Semantics of `nh_assign`:

- Evaluate `expression` as a Python expression using `step_globals` and `step_locals`.
- If `target_path` is a bare `name`:
  - Assign into `step_locals[name]`.
  - Validation:
    - If extracted type information is available for the corresponding `<:name>` binding, validate/coerce to that type.
    - Otherwise, assign without validation.
- If `target_path` is dotted (`name.field...`):
  - Resolve the root object from `step_locals[name]`.
  - Traverse attributes for each intermediate segment.
  - Assign using attribute assignment on the final segment.
  - Validation:
    - Validate only when runtime type metadata is available; otherwise assign without validation.

Commit and mutation notes:

- Commit selection is controlled only by `<:name>` bindings.
- `<:name>` selects which top-level names are committed from `step_locals` into Python locals at Natural block boundaries.
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

At the end of each execution, the LLM returns a final JSON object that represents exactly one outcome variant.

Purpose:

- The outcome is a control-flow signal to the host Python runtime.
- It is not a user-facing "answer" payload.
- The implementation uses strict parsing. Output JSON only, with only the fields allowed for the chosen `kind`.

The outcome is a discriminated union keyed by the required field `kind`.

Outcome kinds:

- `pass`:
  - Success with no control-flow change.
  - Payload keys: `kind` only.

- `return`:
  - Return from the surrounding Python function immediately.
  - Payload keys: `kind`, and required `return_reference_path`.
  - `return_reference_path` must be a dot-separated identifier path into step locals.
  - The host resolves `return_reference_path` within step locals only, using attribute access only.
  - The host then validates/coerces the resolved Python value to the function's return type annotation.

- `break` / `continue`:
  - Loop control.
  - Payload keys: `kind` only.
  - These outcomes are valid only when the Natural block appears syntactically inside a Python `for` or `while` loop. If requested outside a loop, execution fails.

- `raise`:
  - Failure.
  - Payload keys: `kind`, `raise_message`, and optional `raise_error_type`.
  - `raise_error_type` is optional. If provided, it MUST be one of the exception type names listed in the prompt.
  - The host enforces this using the structured output JSON Schema: when `raise_error_type` is allowed for a block, its schema is an `enum` over the allowed exception type names.
  - When `raise_error_type` is provided, the host raises that exception type with the provided `raise_message`.

The implementation chooses strict parsing. Any non-JSON final response is an error.

Notes:

- The allowed outcome set for a Natural block is derived from syntactic context (hard cap) and deny-only frontmatter.
- Python locals are committed at Natural block boundaries based on `<:name>` bindings.

Frontmatter (optional):

A Natural program may start with YAML frontmatter.

Frontmatter is recognized only if the first non-empty line of the Natural program is `---`.

Notes:

- Frontmatter parsing occurs after Natural program rendering (sentinel removal + dedent, and f-string evaluation when the author opted in via an inline f-string Natural block).
- The frontmatter delimiter lines must contain only `---` (no indentation, no trailing whitespace).
- Leading blank lines before frontmatter are ignored and are not included in the program text passed to the model.

Syntax:

- The frontmatter begins with a line containing only `---`.
- It ends with the next line containing only `---`.
- The YAML content between the delimiters must be a mapping.

Directive: `deny`

- `deny` is required when frontmatter is present.
- `deny` must be a YAML sequence of strings.
- Unknown keys are errors.
- Unknown outcome type names are errors.

Allowed outcome type names in `deny` are a subset of the baseline outcome types:

- `pass`
- `return`
- `break`
- `continue`
- `raise`

Semantics:

- Syntactic context defines a hard cap on allowed outcomes:
  - Outside a loop: `pass`, `return`, `raise`.
  - Inside a loop: `pass`, `return`, `break`, `continue`, `raise`.
- Frontmatter deny declarations may only exclude outcome types; they must not expand the syntactic cap.
- If frontmatter denies an outcome type, and the model returns that outcome type, the host raises an `ExecutionError`.

Implementation note:

- Frontmatter is stripped from the program text before it is placed into the model-facing prompt.

### 8.5. Stub executor (test-only)

Nighthawk previously supported a "stub backend" as a library feature. This has been removed.

For tests, this repo includes a test-only `StubExecutor` under `tests/execution/stub_executor.py`. It does not call an LLM. Instead, it reads a step envelope from the Natural program text.

- Parsing rule: inside the Natural block text, stub mode finds the first `{` character and parses the substring starting there as a JSON object.
- The JSON object must be an envelope with:
  - `step_outcome`: an object matching the StepOutcome schema
  - `bindings`: an object mapping names to values
- The stub executor returns `step_outcome` from the envelope.
- The stub executor returns a bindings object filtered to include only names present in the `<:name>` binding list for that Natural block.

## 9. Return value

In the simplest docstring pattern, the Python function body returns a variable that is updated by execution:

- `return result`

If a step requests `outcome.kind == "return"`, the runner returns the validated return value immediately.

## 10. Environment

Nighthawk uses an implicit environment (dynamic scoping) carried via `contextvars.ContextVar`.

The environment is required for step execution and contains:

- `run_id`: the Id of the outermost environment (trace root). This serves as the golden thread that connects distributed agent processes (e.g. parent, child, grandchild) across process boundaries in observability tools.
- `scope_id`: the Id of the current (possibly nested) run scope. This serves as the identity of the current logical execution context.
- `run_configuration` (required): execution configuration.
- `workspace_root` (required): base directory for include resolution and host file operations.
- `agent_root` (optional): working directory used for agent execution (for example, Coding Agent backends). When unset, backends omit the working-directory option and use the provider default (typically the parent process current working directory).
- `step_executor` (required): a strategy object responsible for executing steps (Natural blocks).
- `system_prompt_suffix_fragments` and `user_prompt_suffix_fragments`: optional sequences of strings appended to the end of the effective system/user prompts for the duration of a scoped override.

API:

- `nighthawk.run(environment: Environment)`
  - Replaces the current context environment with the provided environment.
  - Generates a new `scope_id` for the duration of the `with`.
  - Maintains the existing `run_id` if present in the provided environment (used for trace propagation across distributed agent boundaries). Generates a new `run_id` (trace root) only if the provided environment has no `run_id`.
  - Can be used even when no environment is currently set.
- `nighthawk.scope(...)`
  - Enter a nested scope within the current run.
  - Requires an existing environment.
  - Generates a new `scope_id` (keeps the current `run_id`).
  - Only specified fields are overridden for the duration of the `with`.
  - Supports appending prompt suffix fragments for system and user prompts.
- `nighthawk.get_environment() -> Environment`
  - Get the current environment. Raises if unset.

## 11. Interpolation (opt-in, f-strings only)

### 11.1. Rationale

Natural blocks often need to embed computed values (for example, paths or JSON envelopes in tests). To keep rendering predictable and explicit, Nighthawk supports interpolation only when the author opts in using Python f-string syntax.

### 11.2. Mechanism

- Docstring Natural blocks are always literal. They are never interpolated.
- Interpolated Natural blocks are inline f-string Natural blocks (standalone f-string expression statements).
- Interpolation follows standard Python f-string semantics.
  - Expression evaluation rules are those of Python.
  - Brace escaping uses `{{` and `}}` in the f-string source to produce literal `{` and `}` in the rendered text.

Note:

- This interpolation mechanism is distinct from the `nh_eval` tool. f-string evaluation runs in the normal Python execution context, while `nh_eval` evaluates expressions inside the Natural execution environment (`step_globals` + `step_locals`).

Decision:

- Any Python expression is permitted inside f-string `{...}` segments under the trusted-input model.
- There is no implicit placeholder replacement or template preprocessing step for Natural blocks.

## 12. Persistence and user-managed state

Nighthawk does not define a built-in persistence or memory model.

If you want a long-lived object, define it yourself and bind it as an ordinary Python value (for example, a module global named `memory`). Because expression evaluation and assignment operate on `step_locals`, the value bound to `memory` behaves like any other local: it can be read via expressions and mutated via dotted assignment targets.

## 13. Error handling

Nighthawk distinguishes:

- Parse errors: invalid or non-JSON LLM output.
- Tool evaluation errors: invalid expressions, runtime errors from expression evaluation.
- Tool validation errors: type validation/coercion fails for `nh_assign`.

All errors are surfaced as Python exceptions.
