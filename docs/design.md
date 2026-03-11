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
- Recommended model for quality: `openai-responses:gpt-5.4`.
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
  - Required fields include `step_id` (unique Id for the step).
  - Model selection and prompt policy are owned by `StepExecutorConfiguration`; StepContext does not carry model configuration.
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

- `StepExecutorConfiguration`
  - `model`: Model identifier in `provider:model` format. Default: `openai-responses:gpt-5-nano`.
    - Examples: `openai-responses:gpt-5-mini`, `openai-responses:gpt-5-nano`.
    - Special cases:
      - `claude-code:default` and `codex:default` select the backend/provider default model (no explicit model selection is sent to the backend).
  - `model_settings`: optional model/backend settings object forwarded to Pydantic AI Agent calls.
  - `tokenizer_encoding`: tokenizer encoding identifier for approximate token budgeting. `None` means auto-resolve by model name, then fallback to `o200k_base`.
  - `prompts`: prompt templates used for execution.
    - `step_system_prompt_template`: system prompt template that defines the step execution protocol.
    - `step_user_prompt_template`: full user prompt template including section delimiters.
  - `context_limits`: limits for rendering dynamic context into the prompt.
  - `json_renderer_style`: JSON rendering style used in prompt context and tool result envelopes.
  - `system_prompt_suffix_fragments`: optional baseline system prompt suffix fragments for this executor configuration.
  - `user_prompt_suffix_fragments`: optional baseline user prompt suffix fragments for this executor configuration.

- `StepExecutorConfigurationPatch`
  - Partial override object for scoped configuration updates.
  - Supports patching model, model settings, templates, limits, renderer style, tokenizer encoding, and prompt suffix fragment tuples.

### 5.3. Supporting types

- `StepPromptTemplates`
  - Prompt templates used for step execution.
  - `step_system_prompt_template`: system prompt template.
  - `step_user_prompt_template`: user prompt template.

- `StepContextLimits`
  - Limits for rendering dynamic context into the LLM prompt.
  - Fields: `locals_max_tokens`, `locals_max_items`, `globals_max_tokens`, `globals_max_items`, `value_max_tokens`, `tool_result_max_tokens`.

- `JsonableValue`
  - Type alias for JSON-serializable Python values (`dict | list | str | int | float | bool | None`).

- `ExecutionContext`
  - Frozen dataclass representing runtime execution identity.
  - `run_id`: the Id of the outermost run (trace root).
  - `scope_id`: the Id of the current scope.

### 5.4. Runtime accessors

- `nighthawk.get_current_step_context() -> StepContext`
  - Get the `StepContext` for the currently executing Natural block. Raises if no step is active.

### 5.5. Backend-specific settings

Backend-specific settings are passed via `model_settings` in `StepExecutorConfiguration`. Each backend validates and applies its own settings.

#### CodexModelSettings (for `codex:*` models)

- `allowed_tool_names`: Nighthawk tool names exposed to the model.
- `codex_executable`: Path or name of the Codex CLI executable. Default: `"codex"`.
- `model_reasoning_effort`: Reasoning effort level (`"minimal"`, `"low"`, `"medium"`, `"high"`, `"xhigh"`).
- `sandbox_mode`: Codex sandbox isolation mode (`"read-only"`, `"workspace-write"`, `"danger-full-access"`).
- `working_directory`: Absolute path to the working directory for Codex.

#### ClaudeCodeModelSettings (for `claude-code:*` models)

- `permission_mode`: Claude Code permission mode (`"default"`, `"acceptEdits"`, `"plan"`, `"bypassPermissions"`). Default: `"default"`.
- `setting_sources`: Configuration sources to load (`"user"`, `"project"`, `"local"`).
- `allowed_tool_names`: Nighthawk tool names exposed to the model.
- `claude_allowed_tool_names`: Additional Claude Code native tool names to allow.
- `claude_max_turns`: Maximum conversation turns. Default: `50`.
- `working_directory`: Absolute path to the working directory for Claude Code.

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

- `<name>`: read binding. The current Python value of `name` is made available to the LLM.
- `<:name>`: write binding. The LLM may update the value of `name`.

Resolution note:

- Read binding reads resolve names using Python lexical rules (LEGB: locals, enclosing, globals, builtins).
- If a name is missing or unbound, the error is surfaced as a Python exception type where feasible (for example `NameError`, `UnboundLocalError`).

Constraints:

- `name` is a simple identifier (no dotted paths).
- `<:name>` does not require prior declaration.
  - Practical note: if subsequent Python code reads a variable that has not been assigned yet, Python will raise before any LLM behavior can help. Initialize variables in Python when needed.

Type note:

- Nighthawk extracts type information for `<:name>` bindings from the function source AST at compile time.
- If no type annotation is found, the type is treated as `object`.

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
- It is initialized at the start of each Natural block execution, in the following order:
  1. If a parent step context exists on the step context stack, start from its `step_locals` values.
  2. Overlay the caller frame's current `python_locals` (so current Python locals always win over inherited step-context state).
  3. For each read binding (`<name>`), resolve the name using Python lexical rules (locals, enclosing cell scopes, name scopes, globals, builtins) and place the resolved value into `step_locals`.
- During execution, the LLM can update `step_locals` via tools (Section 8.3).
- At the end of execution, values for `<:name>` bindings are committed into Python locals.


### 8.2. Prompt context

To reduce black-box behavior, Nighthawk includes bounded prompt context sections in the user prompt.

#### 8.2.1. User prompt structure

The default user prompt template renders three delimited sections:

- `<<<NH:PROGRAM>>>` / `<<<NH:END_PROGRAM>>>`: the Natural program text (after sentinel removal, `textwrap.dedent`, and f-string evaluation when applicable).
- `<<<NH:LOCALS>>>` / `<<<NH:END_LOCALS>>>`: the locals summary (see 8.2.2).
- `<<<NH:GLOBALS>>>` / `<<<NH:END_GLOBALS>>>`: the globals summary (see 8.2.3).

The template uses `$program`, `$locals`, and `$globals` placeholders, substituted at prompt construction time.

#### 8.2.2. Locals summary

The locals summary renders selected names from `step_locals`.

Selection:

- All names in `step_locals` are eligible, except names starting with `__` (dunder).

Ordering:

- Entries are rendered in lexicographic order by name.

Rendering format:

- Non-callable values: `name: type_name = json_value`, where `json_value` is the compact JSON rendering of the value (bounded by `context_limits.value_max_tokens`).
- Callable values: `name: (signature)`, where `(signature)` is the result of `inspect.signature`. Type annotations are included when available (e.g., `(base: int, bonus: int) -> int`).
  - If the callable has a meaningful docstring, the first line is appended as `# intent: first_line`.
  - If multiple callable entries share the same signature text, each is annotated with `# disambiguation: use name` to help the LLM distinguish them.
  - If the signature cannot be resolved (e.g., `__signature__` raises), the entry renders as `name: <callable; signature-unavailable>`.
- `TypeAliasType` values (PEP 695): `name: type = underlying_type`.

Truncation:

- Rendering is bounded by `context_limits.locals_max_tokens` and `context_limits.locals_max_items`.
- When the limit is reached before all entries are rendered, a `<snipped>` marker is appended and a diagnostic log message is emitted on the `nighthawk` logger.

#### 8.2.3. Globals summary

The globals summary renders module-level names that are referenced in the Natural program text but are not present in `step_locals`.

Reference extraction:

- The Natural program text is scanned for unescaped `<name>` tokens (both read bindings `<name>` and dotted references `<name.field>`).
- For dotted references, only the top-level name (before the first `.`) is extracted.
- Escaped references (`\<name>`) are not extracted. The backslash is removed in the program text passed to the model.

Selection:

- A referenced name is included in the globals summary only if it is NOT present in `step_locals`.
- The name is resolved from `step_globals` (which contains module globals available to the function).
- If resolution fails, the name is silently omitted.

Ordering:

- Entries are rendered in lexicographic order by name.

Rendering format:

- Same rules as the locals summary (Section 8.2.2).

Truncation:

- Rendering is bounded by `context_limits.globals_max_tokens` and `context_limits.globals_max_items`.
- Truncation behavior is the same as the locals summary.


### 8.3. Tools available to the LLM

Nighthawk exposes two paths for the LLM to call Python functions:

1. **Binding functions** (Section 8.2): Callable values in step locals or step globals are rendered as text signatures in the prompt context. The LLM invokes them via `nh_eval` or `nh_exec`.
2. **User-defined tools** (`@nighthawk.tool`): Registered callables are presented via the model's native tool-calling interface. Each tool definition adds a JSON Schema to every API request.

Binding functions incur no per-definition token overhead beyond the signature line in the prompt context. User-defined tools incur per-definition overhead proportional to the tool's JSON Schema size.

Tools are Python callables exposed to the LLM via pydantic-ai tool calling.

User-defined tools:

- The host defines tools using the `@nighthawk.tool` decorator.

Registration API:

- `@nighthawk.tool`: Decorator that registers a callable as a Nighthawk tool.
  - `name`: Optional name override. Defaults to the function `__name__`.
  - `overwrite`: If True, replaces any existing tool with the same name.
  - `description`: Optional description override. Defaults to the function docstring.
  - `metadata`: Arbitrary metadata dict attached to the tool definition.
- Tool names must be ASCII and match `^[A-Za-z_][A-Za-z0-9_]*$`.
- Tool registration targets the innermost active scope (call scope > tool scope > global).
- Name conflicts raise `ToolRegistrationError` unless `overwrite=True`.

Example:

```py
@nighthawk.tool(name="add_points")
def add_points(run_context, *, base: int, bonus: int) -> int:
    """Return a deterministic sum for score calculation."""
    _ = run_context
    return base + bonus
```

The first parameter `run_context` is a Pydantic AI `RunContext[StepContext]` injected automatically by the framework. It is not exposed to the LLM as a tool argument.

Scoping:

- `nighthawk.run()` and `nighthawk.scope()` each open a nested tool scope.
- Tools registered inside a scope are visible only within that scope.

Provided tools (built-in):

- Provided tools are always available by default.
- Provided tools are exposed with names prefixed by `nh_` to reduce collisions.

Tools operate against `step_locals` and `step_globals`.

Decision (step_globals):

- `step_globals` is initialized from the function's Python module globals (`python_globals`), ensuring that module-level names (functions, classes, constants, imports) are available for expression evaluation. This mirrors Python's standard name resolution semantics (LEGB: locals, enclosing, globals, builtins).
- `__builtins__` is guaranteed to be present in `step_globals`; if missing from the module globals, it is injected.

Expressions are evaluated against `step_globals` + `step_locals`.

Inspect tool:

- `nh_eval(expression: str) -> object`
  - Evaluate a Python expression and return the result. Use to inspect values and call functions.
  - If the evaluated expression is awaitable, it is awaited before returning.

Mutation tool:

- `nh_exec(expression: str) -> object`
  - Execute a Python expression for its side effect on mutable objects (e.g., `list.append()`, `dict.update()`, `set.add()`).
  - Returns the expression result.

Implementation note: `nh_eval` and `nh_exec` share the same underlying implementation (Python `eval()`). The two-tool split exists as a semantic signal to the LLM (inspect intent vs mutate intent), not a runtime distinction.

Binding tool:

- `nh_assign(target_path: str, expression: str) -> object`

Target grammar:

- `target_path := name ("." field)*`
- `name` and `field` are ASCII Python identifiers.

Reserved targets:

- Any segment starting with `__` (dunder) is disallowed.

Semantics of `nh_assign`:

- Evaluate `expression` as a Python expression using `step_globals` and `step_locals`.
- If the evaluated expression is awaitable, it is awaited before assignment.
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

Tool result JSON format:

All tool results are wrapped in a JSON envelope with the following structure:

- Success: `{"value": <bounded JSON rendering>, "error": null}`
- Failure: `{"value": null, "error": {"kind": "<category>", "message": "<detail>", "guidance": "<recovery hint>"}}`

Error kind categories: `invalid_input`, `resolution`, `execution`, `transient`, `internal`.

The `value` field is bounded by `context_limits.tool_result_max_tokens` and may be summarized using headson (head-of-JSON) truncation when the full rendering exceeds the token budget.

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
  - If the surrounding function is async and the resolved value is awaitable, the host awaits it before validation.
  - The host then validates/coerces the resolved Python value to the function's return type annotation.
  - If the surrounding function is sync and the resolved value is awaitable, execution fails.

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

## 9. Return value

In the simplest docstring pattern, the Python function body returns a variable that is updated by execution:

- `return result`

If a step requests `outcome.kind == "return"`, the runner returns the validated return value immediately.

## 10. Runtime scoping

Nighthawk uses dynamic scoping carried via `contextvars.ContextVar`.

The required runtime object for step execution is:

- `step_executor` (required): a strategy object responsible for executing steps (Natural blocks).

Runtime execution identity is modeled separately in `ExecutionContext`:

- `run_id`: the Id of the outermost run (trace root). This serves as the golden thread that connects distributed agent processes (e.g. parent, child, grandchild) across process boundaries in observability tools.
- `scope_id`: the Id of the current (possibly nested) run scope. This serves as the identity of the current logical execution context.

Nighthawk does not own workspace filesystem concerns (such as include resolution or host file operations). Those concerns belong to the host application layer that embeds Nighthawk.

Working directory selection for provider backends is configured via `ModelSettings["working_directory"]` (absolute, resolved). When unset, backends omit the working-directory option and use the provider default (typically the parent process current working directory).
API:

- `nighthawk.run(step_executor: StepExecutor, *, run_id: str | None = None)`
  - Replaces the current context step executor with the provided step executor.
  - Generates a new `ExecutionContext` for the duration of the `with`.
  - Uses provided `run_id` when given; otherwise generates a new `run_id` (trace root).
  - Always generates a fresh `scope_id`.
  - Can be used even when no step executor is currently set.
- `nighthawk.scope(*, step_executor_configuration: StepExecutorConfiguration | None = None, step_executor_configuration_patch: StepExecutorConfigurationPatch | None = None, step_executor: StepExecutor | None = None, system_prompt_suffix_fragment: str | None = None, user_prompt_suffix_fragment: str | None = None) -> Iterator[StepExecutor]`
  - Enter a nested scope within the current run.
  - Requires an existing step executor.
  - Generates a new `scope_id` (keeps the current `run_id`).
  - Only specified fields are overridden for the duration of the `with`.
  - `system_prompt_suffix_fragment`: optional string appended to the system prompt for the duration of the scope.
  - `user_prompt_suffix_fragment`: optional string appended to the user prompt for the duration of the scope.
  - Yields the resolved `StepExecutor` for the scope.
- `nighthawk.get_step_executor() -> StepExecutor`
  - Get the current step executor. Raises if unset.
- `nighthawk.get_execution_context() -> ExecutionContext`
  - Get the current runtime execution identity. Raises if unset.

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

If you want a long-lived object, define it yourself and bind it as an ordinary Python value. Because expression evaluation and assignment operate on `step_locals`, bound values behave like any other local: they can be read via expressions and mutated in-place via `nh_exec`.

### 12.1. Carry pattern

The carry pattern is an idiomatic use of read bindings for cross-block context continuity. Pass a mutable object (e.g., `list[str]`) as a read binding (`<carry>`) and instruct the LLM to mutate it in-place via `nh_exec`. Read bindings prevent rebinding, so the caller's reference is preserved while the object contents are updated.

For practical examples and design tips, see [Tutorial Section 5](tutorial.md#5-cross-block-composition).

## 13. Error handling

Nighthawk defines a hierarchy of exceptions rooted at `NighthawkError`.

Exception hierarchy:

- `NighthawkError`: Base class for all Nighthawk exceptions.
  - Raised when: runtime preconditions fail (e.g. no active run context, missing step executor).
- `NaturalParseError(NighthawkError)`: Natural block parsing or frontmatter parsing failed.
  - Raised when: the sentinel is missing, bindings are invalid, frontmatter YAML is malformed, or AST extraction fails.
- `ExecutionError(NighthawkError)`: Natural block execution failed.
  - Raised when: the LLM returns invalid JSON, an outcome kind is disallowed, return value validation fails, or `raise` outcome is triggered without a matching exception type.
- `ToolEvaluationError(NighthawkError)`: Expression evaluation inside a tool call failed.
  - Raised when: `eval()` raises during `nh_eval`, `nh_exec`, or `nh_assign` expression evaluation.
- `ToolValidationError(NighthawkError)`: Type validation/coercion failed during `nh_assign`.
  - Raised when: the assigned value does not match the expected binding type.
- `ToolRegistrationError(NighthawkError)`: Tool registration failed.
  - Raised when: a tool name is invalid, or a name conflict occurs without `overwrite=True`.

All exceptions are surfaced as Python exceptions and can be caught with standard `try`/`except`.

## 14. Tool result contract

All tool results returned to the LLM are wrapped in a JSON envelope:

- Success: `{"value": <bounded JSON rendering>, "error": null}`
- Failure: `{"value": null, "error": {"kind": "<category>", "message": "<detail>", "guidance": "<recovery hint>"}}`

Error kind categories: `invalid_input`, `resolution`, `execution`, `transient`, `internal`.

The `value` field is bounded by `context_limits.tool_result_max_tokens` and may be summarized using headson (head-of-JSON) truncation when the full rendering exceeds the token budget.

Supporting types (internal):

- `ToolBoundaryError`: Exception carrying `kind` (ErrorKind), `message`, and optional `guidance`. Raised by tool implementations to signal structured failures.
- `ToolResultRenderingPolicy`: Frozen dataclass controlling how tool results are rendered (tokenizer encoding name, max tokens, JSON renderer style).
