# Nighthawk specification

This document is the specification for Nighthawk.

## 0. Document scope and alignment policy

This document specifies:

- What counts as a Natural block (docstring and inline).
- The Natural DSL binding syntax (`<name>`, `<:name>`).
- The execution model (state layers, tools, and the final JSON contract).
- The host-facing environment API and configuration surface.

This document does not attempt to describe every compilation or implementation detail. The current implementation in `src/nighthawk/` is expected to match this specification.

Nighthawk treats this document as the target behavior. If you find a mismatch between this document and the implementation:

- Prefer changing the implementation to match this document.
- If the document is wrong or outdated, update the document and adjust tests so the spec and implementation remain aligned.

This file intentionally does not maintain a persistent divergence ledger.

The condensed coding agent guide (`for-coding-agents.md`) is a derivative document. It distills actionable rules from this specification and the learner-facing pages. If the guide contradicts this document, this document prevails.

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
- A full "Skills" framework (Nighthawk delegates to the backend CLI's native skill system; see [Coding agent backends](coding-agent-backends.md#skills)).
- Executing Python code blocks embedded in markdown (a broader "Natural -> Python -> Natural" nesting beyond docstrings).

## 3. Hard constraints

- Python 3.13+.
- Default and recommended models: see [Pydantic AI providers](pydantic-ai-providers.md).
- Coding agent backends are installed via extras: `claude-code-sdk`, `claude-code-cli`, `codex`. Pydantic AI provider dependencies are installed separately (see [Pydantic AI providers](pydantic-ai-providers.md)).
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
    - `model`: Model identifier in `provider:model` format. Default: `openai-responses:gpt-5.4-nano` (see [Pydantic AI providers](pydantic-ai-providers.md)).
        - Examples: `openai-responses:gpt-5.4-mini`, `openai-responses:gpt-5.4-nano`.
        - Special cases:
            - `claude-code-sdk:default`, `claude-code-cli:default`, and `codex:default` select the backend/provider default model (no explicit model selection is sent to the backend).
    - `model_settings`: optional model/backend settings. Accepts a `dict[str, Any]` or a backend-specific `BaseModel` instance (auto-converted to dict). Forwarded to Pydantic AI Agent calls. Each coding agent backend defines a settings class (`CodexModelSettings`, `ClaudeCodeSdkModelSettings`, `ClaudeCodeCliModelSettings`) -- see [Coding agent backends](coding-agent-backends.md) for field-level documentation.
    - `tokenizer_encoding`: tokenizer encoding identifier for approximate token budgeting. `None` means auto-resolve by model name, then fallback to `o200k_base`.
    - `prompts`: prompt templates used for execution.
        - `step_system_prompt_template`: system prompt template that defines the step execution protocol.
        - `step_user_prompt_template`: full user prompt template including section delimiters.
    - `context_limits`: limits for rendering dynamic context into the prompt.
    - `json_renderer_style`: [headson](https://github.com/kantord/headson) rendering style used in prompt context and projected tool-result previews. Default: `"default"`. Available values: `"strict"` (valid JSON, no annotations), `"default"` (pseudo-JSON with omission markers like `…`), `"detailed"` (JS-like with inline comments such as `// N more`).
    - `system_prompt_suffix_fragments`: optional baseline system prompt suffix fragments for this executor configuration.
    - `user_prompt_suffix_fragments`: optional baseline user prompt suffix fragments for this executor configuration.

### 5.3. Supporting types

- `StepPromptTemplates`
    - Prompt templates used for step execution.
    - `step_system_prompt_template`: system prompt template.
    - `step_user_prompt_template`: user prompt template.
- `StepContextLimits`
    - Limits for rendering dynamic context into the LLM prompt.
    - Fields: `locals_max_tokens`, `locals_max_items`, `globals_max_tokens`, `globals_max_items`, `value_max_tokens`, `object_max_methods`, `object_max_fields`, `object_field_value_max_tokens`, `tool_result_max_tokens`.
- `JsonableValue`
    - Type alias for JSON-serializable Python values (`dict | list | str | int | float | bool | None`).
- `ExecutionRef`
    - Frozen dataclass representing runtime execution identity.
    - `run_id`: the Id of the outermost run (trace root).
    - `scope_id`: the Id of the current scope.
    - `step_id`: the Id of the current step when available, otherwise `None`.

### 5.4. Runtime accessors

- `nighthawk.get_current_step_context() -> StepContext`
    - Get the `StepContext` for the currently executing Natural block. Raises if no step is active.

See [Section 10](#10-runtime-scoping) for additional runtime accessors (`get_step_executor`, `get_execution_ref`).

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

Runtime type inference:

- When a write binding has no explicit type annotation, the AST transformer assigns `object` as a placeholder type.
- At runtime, before LLM execution, Nighthawk upgrades `object` placeholders to the type of the binding's initial value in `step_locals` (e.g., `result = ""` infers `str`).
- Inference is skipped when the initial value is `None` or a generic `object()` instance.
- This enables `nh_assign` type validation and retry for unannotated bindings that have typed initial values.

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

The locals summary renders selected names from `step_locals`. It is a transparent projection of Python-visible local state at the Natural block boundary. Nighthawk MUST NOT automatically suppress otherwise eligible LOCALS entries based on reference frequency, VLM cost, or multimodal payload type; users control prompt cost by reducing the Python locals that exist at the block boundary.

Selection:

- All names in `step_locals` are eligible, except names starting with `__` (dunder).

Ordering:

- Entries are rendered in lexicographic order by name.

Rendering format:

- `TypeAliasType` values (PEP 695): `name: type = underlying_type`.
- Callable values: `name: (signature)`, where `(signature)` is the result of `inspect.signature`. Type annotations are included when available (e.g., `(base: int, bonus: int) -> int`).
    - If the callable has a meaningful docstring, the first line is appended as `# first_line`.
    - If the callable is async, `async` is appended in metadata comments.
    - If multiple callable entries share the same signature text, each is annotated with `# disambiguation: use name`.
    - If the signature cannot be resolved (e.g., `__signature__` raises), the entry renders as `name: <callable; signature-unavailable>`.
- Object capability values (non-callable, non-scalar, non-container values):
    - Header line: `name: object = TypeName`.
    - Public method lines: `name.method: (signature)` using callable rules above.
    - Public field lines: `name.field: type_name = json_value`.
    - Public means names that do not start with `_`; private and dunder names are excluded.
    - Properties are not evaluated.
    - Field discovery uses safe sources: instance `__dict__`, dataclass fields, Pydantic model fields, and readable public `__slots__` entries.
    - Class attributes that are public and non-callable are included as fields.
    - Method expansion is bounded by `context_limits.object_max_methods`; field expansion is bounded by `context_limits.object_max_fields`; each field value preview is bounded by `context_limits.object_field_value_max_tokens`.
    - If object member limits are exceeded, explicit omission lines are added: `name.<methods>: <snipped N public methods>` and/or `name.<fields>: <snipped N public fields>`.
- Other non-callable values: `name: type_name = json_value`, where `json_value` is bounded by `context_limits.value_max_tokens`.
- Top-level Pydantic AI multimodal values are rendered as `name: TypeName = ` followed by inline user content in the prompt payload.
    - This applies to top-level locals/globals entries.
- Top-level `list` / `tuple` values are also rendered as inline user content when every item is valid Pydantic AI `UserContent` and at least one item is multimodal.
    - The binding line still renders once, for example `photos: list = `, followed by the original top-level item order.
    - Pure text-only sequences do not use this rule; they remain preview-rendered values.
- Explicit dotted references whose resolved leaf resolves to hoistable inline user content add a separate section line for that full dotted path (for example `holder.photo: BinaryContent = ` followed by inline user content).
    - Only explicitly referenced dotted paths are added this way.
    - This is a leaf-only rendering rule, not recursive object-graph extraction.
    - Dotted-path resolution is attribute-oriented only. Each segment after the top-level name is resolved against discovered object fields using the same safe field sources as object capability rendering.
    - Dotted-path resolution does not treat `Mapping` keys as attribute segments. For mapping access, use Python expressions or helper functions (for example `nh_eval("payload['photo']")`) rather than `<payload.photo>`.
    - `list` / `tuple` leaves follow the same hoisting rule as top-level bindings: the sequence is hoisted when every item is valid Pydantic AI `UserContent` and at least one item is multimodal.
    - Non-multimodal dotted references remain preview text inside object capability rendering.
    - When a dotted multimodal reference is rendered as a separate section line, the corresponding field is omitted from the owning object capability block to avoid duplication. The field still consumes a slot in the `object_max_fields` budget.

Callable disambiguation considers both top-level callable entries and object method entries in the same section.

Ordering:

- Top-level entries are rendered in lexicographic order by top-level name.
- Object methods and fields are rendered in lexicographic order by member name.
- Methods are rendered before fields for each object entry.
- If a section-level budget prevents rendering all top-level entries, `<snipped>` is appended at the end of the section.

Safety:

- Prompt rendering does not call user methods.
- Prompt rendering does not evaluate properties or descriptors requiring attribute execution.
- Slot and field access failures are ignored for rendering purposes.

Token budgeting:

- Section budgets remain governed by `locals_max_tokens` / `globals_max_tokens` and item budgets by `locals_max_items` / `globals_max_items`.
- Object method and field expansion is additionally governed by object-specific limits listed above.
- Line-level token counting includes newline separators during budget checks.
- Each rendered multimodal content item is charged a fixed internal budgeting heuristic against the section budget so that multimodal-heavy sections still trigger truncation. This is not a provider-side token estimate.

Observability:

- Section-level token truncation emits `prompt_context_truncated` logs with section, reason, and configured max token details.
- Object member omission due to object-specific member limits does not emit a separate log event.

Truncation:

- Rendering is bounded by `context_limits.locals_max_tokens` and `context_limits.locals_max_items`.
- When the limit is reached before all entries are rendered, a `<snipped>` marker is appended and a diagnostic log message is emitted on the `nighthawk` logger.
- Because section rendering remains lexicographic, tight item or token budgets can affect which bindings remain visible, including explicitly referenced dotted multimodal leaves.

#### 8.2.3. Globals summary

The globals summary renders module-level names that are referenced in the Natural program text but are not present in `step_locals`.

Reference extraction:

- The Natural program text is scanned for unescaped `<name>` tokens (both read bindings `<name>` and dotted references `<name.field>`).
- For dotted references, the top-level name (before the first `.`) participates in globals selection, and the full dotted path remains available for multimodal leaf rendering.
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

1. **Binding functions** (Section 8.2): Callable values in step locals or step globals are rendered as text signatures in the prompt context. The LLM invokes them via `nh_eval`.
2. **User-defined tools** (`@nighthawk.tool`): Registered callables are presented via the model's native tool-calling interface. Each tool definition adds a JSON Schema to every API request.

Binding functions incur no per-definition token overhead beyond the signature line in the prompt context. User-defined tools incur per-definition overhead proportional to the tool's JSON Schema size.

Design intent: Each parameter in a binding function signature represents a decision point the LLM must evaluate. The two-path design reflects this: binding functions carry minimal, LLM-friendly signatures while complex operations are composed in Python and exposed through simple binding functions. See [Natural blocks](natural-blocks.md#designing-binding-functions) for practical design patterns.

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
@nighthawk.tool(name="get_step_id")
def get_step_id(run_context: RunContext[StepContext]) -> str:
    """Return the current step Id."""
    return run_context.deps.step_id
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

Eval tool:

- `nh_eval(expression: str) -> object`
    - Evaluate a Python expression and return the result. Use to inspect values, call functions, and mutate objects in-place.
    - In-place mutations performed via `nh_eval` are not runtime-validated.
    - If the evaluated expression is awaitable, it is awaited before returning.

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
        - Validate only the final assigned field when runtime type metadata is available; otherwise assign without validation.

Commit and mutation notes:

- Commit selection is controlled only by `<:name>` bindings.
- `<:name>` selects which top-level names are committed from `step_locals` into Python locals at Natural block boundaries.
- Dotted `nh_assign` on a write binding root marks that root as dirty for commit selection.
- Dotted `nh_assign` on a read binding root does not participate in commit selection.
- Final validation is applied only to committed write bindings at step finalization.
- Dotted mutation on read bindings and in-place mutation via `nh_eval` are outside the final validation guarantee.

Write tool return value:

- The tool returns a diagnostic object describing:
    - whether it succeeded
    - a bounded summary of the updated value (on success)
    - validation details (when relevant)
    - error details (on failure)

Tool result transport contract:

- Internally, tool execution produces a canonical `ToolOutcome` with:
    - `payload`: the success payload, which may include native Pydantic AI multimodal values
    - `error`: `null` on success or a structured error object on failure
- The host also derives a projected preview observation for text-only transports, logging, and tracing.
    - This projection may be derived lazily at the transport or tracing boundary rather than eagerly at tool execution time.
- Text-projected backends that expose Nighthawk tools automatically add system-prompt guidance that tool-result previews may be lossy and are bounded by `context_limits.tool_result_max_tokens`.
- The projected preview uses the following JSON shape:
    - Success: `{"value": <bounded JSON rendering>, "error": null}`
    - Failure: `{"value": null, "error": {"kind": "<category>", "message": "<detail>", "guidance": "<recovery hint>"}}`
- Multimodal-capable transports MAY send top-level multimodal payload items natively instead of embedding them into the preview text.
    - When they do, they preserve the original top-level content item order from the tool payload, including text/media adjacency for qualifying mixed `list` / `tuple` payloads.
    - The MCP tool-return transport carries only image (`BinaryContent.is_image`) and audio (`BinaryContent.is_audio`) items natively (as `mcp.types.ImageContent` / `mcp.types.AudioContent`). All other `BinaryContent` and `FileUrl` items, including document and video, project to `mcp.types.TextContent` so the transport stays symmetric with text-projected backends. Native MCP video carriage is intentionally not part of this contract because video-modal requirements are still unstable. Native MCP document carriage may be added only behind an explicit transport capability; until then, document items also remain text-projected. Multimodal-capable providers that bypass MCP still send supported items natively via `ToolReturnPart.files`.
- Provider-backed backends that use Pydantic AI's standard tool loop SHOULD surface general `ToolOutcome.error` failures as ordinary tool results rather than retry prompts.
    - The dedicated retry-prompt path is reserved for tool argument validation failures before tool execution (for example via `RetryPromptPart.model_response()`).
    - When a provider-backed backend surfaces a tool failure without native multimodal content, it SHOULD use the same structured preview envelope shape described above.
- User-prompt transport is backend-dependent.
    - Provider-backed executors that accept Pydantic AI `UserContent` MAY send user-prompt multimodal content natively.
    - Text-projected backends (including coding agent backends that must talk to a CLI text channel) project multimodal user prompt content to text placeholders plus local file paths or URL references instead of sending native VLM input.
- For tool results, backends SHOULD preserve top-level mixed text/multimodal order when the payload is a `list` / `tuple` whose items are all valid `UserContent` and at least one item is multimodal.
- When that ordered-segmentation rule does not apply, text-projected transports SHOULD derive tool-return text from Pydantic AI's `ToolReturnPart.model_response_str_and_user_content()` and project the returned `user_content` separately.
- MCP transports SHOULD use the same fallback split when ordered segmentation does not apply: one text observation derived from `tool_response_text`, followed by the extracted `user_content` items in order.
- If a rich transport fails while projecting an otherwise recoverable tool result, the backend SHOULD degrade to the projected preview text rather than fail the step.
- When Pydantic AI reports a successful empty split (`tool_response_text == ""` and extracted `user_content` is empty), backends MUST preserve that as an empty success rather than falling back to the projected preview envelope.
    - Text-projected transports render only their normal tool-return framing with no preview JSON body.
    - MCP-style native transports return an empty content list for that tool result.
- `BinaryContent` values can be staged to local files for text-projected transports because the host already has the bytes.
- URL-based values such as `ImageUrl` / `FileUrl` MAY be transported natively by multimodal-capable provider backends, but text-projected backends and the MCP tool-return transport treat them as URL references only.
- Nested multimodal values inside dict/model/object structure are not recursively hoisted. They remain part of the preview rendering only, except for top-level or explicitly referenced dotted-leaf `list` / `tuple` bindings whose items are valid `UserContent`. Users who want a nested media item to travel as prompt content should lift that leaf into an explicit helper binding before the Natural block.
- `UploadedFile` values are provider-owned references. Backends that cannot resolve the provider file identifier MUST reject them at the user-prompt boundary.
- For tool results, backends that cannot resolve an `UploadedFile` MUST preserve the rest of the top-level payload and replace only that item with an explanatory text fallback instead of silently dropping it.

Error kind categories: `invalid_input`, `resolution`, `execution`, `transient`, `internal`, `oversight`.

Boundary rule for tool-call failures:

- If the model can recover by changing tool arguments, choosing another tool, or continuing without the tool, the failure MUST be projected back to the model as a structured error observation.
- Host invariant violations (for example, broken runtime contract or invalid host-side oversight hook contract) MAY propagate as Python exceptions to the host instead of being projected.

The projected `value` field is bounded by `context_limits.tool_result_max_tokens` and may be summarized using [headson](https://github.com/kantord/headson) truncation when the full rendering exceeds the token budget. Headson is a structure-aware JSON summarizer: it parses the full JSON tree, then selects representative nodes to produce a compact preview that preserves the shape and key values of the data within a strict byte budget (analogous to `head`/`tail` but for structured data).

Atomicity requirement:

- `nh_assign` is atomic: if traversal, evaluation, or validation fails, it performs no updates.

#### 8.3.1. Supporting types (internal)

- `ToolBoundaryError`: Exception carrying `kind` (ErrorKind), `message`, and optional `guidance`. Raised by tool implementations to signal structured failures.
- `ToolResultRenderingPolicy`: Frozen dataclass controlling how tool result previews are rendered (tokenizer encoding name, max tokens, JSON renderer style).

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
    - Payload keys: `kind`, and required `return_expression`.
    - `return_expression` is a Python expression evaluated against `step_globals` and `step_locals` (consistent with `nh_eval` and `nh_assign` expression evaluation).
    - If the surrounding function is async and the evaluated value is awaitable, the host awaits it before validation.
    - The host then validates/coerces the evaluated Python value to the function's return type annotation.
    - If the surrounding function is sync and the evaluated value is awaitable, execution fails.
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

### 8.5. Async execution model

Natural functions may be declared `async`. The async execution model extends the sync model with the following behaviors:

- Expression evaluation: if an `nh_eval` or `nh_assign` expression produces an awaitable, the host awaits it before returning the result to the model.
- Return validation: if a `return` outcome's `return_expression` evaluates to an awaitable and the surrounding function is async, the host awaits it before return type validation. If the surrounding function is sync and the evaluated value is awaitable, execution fails with `ExecutionError`.
- Binding function calls: async binding functions produce awaitables that are auto-awaited in async natural functions.
- Sync/async interoperability: if a sync natural function encounters an awaitable from an async binding function, execution fails with `ExecutionError`. The caller must be async to handle awaitable results.
- Concurrency: async natural functions are ordinary coroutines. Concurrent execution via `asyncio.gather` is safe for Natural blocks that do not share mutable bindings, since each block executes with an independent step context.

## 9. Return value

In the simplest docstring pattern, the Python function body returns a variable that is updated by execution:

- `return result`

If a step requests `outcome.kind == "return"`, the runner returns the validated return value immediately.

## 10. Runtime scoping

Nighthawk uses dynamic scoping carried via `contextvars.ContextVar`.

The required runtime object for step execution is:

- `step_executor` (required): a strategy object responsible for executing steps (Natural blocks).

Runtime execution identity is modeled separately in `ExecutionRef`:

- `run_id`: the Id of the outermost run (trace root). This serves as the golden thread that connects distributed agent processes (e.g. parent, child, grandchild) across process boundaries in observability tools.
- `scope_id`: the Id of the current (possibly nested) run scope. This serves as the identity of the current logical execution context.
- `step_id`: the Id of the current step when available. Outside active step execution it is `None`.

Nighthawk does not own workspace filesystem concerns (such as include resolution or host file operations). Those concerns belong to the host application layer that embeds Nighthawk.

Working directory selection for provider backends is configured via `ModelSettings["working_directory"]` (absolute, resolved). When empty (default `""`), backends omit the working-directory option and use the provider default (typically the parent process current working directory).
API:

- `nighthawk.run(step_executor: StepExecutor, *, run_id: str | None = None)`
    - Replaces the current context step executor with the provided step executor.
    - Generates a new `ExecutionRef` for the duration of the `with`.
    - Uses provided `run_id` when given; otherwise generates a new `run_id` (trace root).
    - Always generates a fresh `scope_id`.
    - Can be used even when no step executor is currently set.
- `nighthawk.scope(*, mode: Literal["inherit", "replace"] = "inherit", step_executor_configuration: StepExecutorConfiguration | None = None, step_executor: StepExecutor | None = None, oversight: Oversight | None = None, system_prompt_suffix_fragments: Sequence[str] | None = None, user_prompt_suffix_fragments: Sequence[str] | None = None, implicit_references: Mapping[str, object] | None = None) -> Iterator[StepExecutor]`
    - Enter a nested scope within the current run.
    - Requires an existing step executor.
    - Generates a new `scope_id` (keeps the current `run_id`).
    - `oversight` omitted means inherit the current hooks; explicit `None` clears them for the nested scope.
    - `mode="inherit"` (default):
        - Appends prompt suffix fragment lists.
        - Merges `implicit_references` additively with conflict checks.
    - `mode="replace"`:
        - Replaces provided list/mapping values.
        - `None` means no change.
        - Explicit `[]` / `{}` clears inherited list/mapping values.
        - Explicit list/mapping values (for example `[e1, e2]` or `{k1: v1, k2: v2}`) fully replace inherited values.
    - Yields the resolved `StepExecutor` for the scope.
- `nighthawk.get_step_executor() -> StepExecutor`
    - Get the current step executor. Raises if unset.
- `nighthawk.get_execution_ref() -> ExecutionRef`
    - Get the current runtime execution identity. Raises if unset.

### 10.1. Observability contract (OpenTelemetry span/event)

Nighthawk uses OpenTelemetry spans as the sole runtime trace model.

Runtime spans:

- `nighthawk.run`
- `nighthawk.scope`
- `nighthawk.step`
- `nighthawk.step_executor`

Identity attributes:

- `run.id: str`
- `scope.id: str`
- `step.id: str` (exact format: `python_module:line`, on `nighthawk.step`)

Step events (emitted on `nighthawk.step`):

- `nighthawk.step.completed`
    - attributes:
        - `nighthawk.step.outcome_kind`
- `nighthawk.step.raised`
    - attributes:
        - `nighthawk.step.outcome_kind`
        - `nighthawk.step.raise_message`
        - `nighthawk.step.raise_error_type` (when provided)
- `nighthawk.step.failed`
    - attributes:
        - `nighthawk.step.error_kind`
        - `nighthawk.step.error_message`

Semantics:

- `raise` outcome is treated as domain-level behavior, represented by `nighthawk.step.raised`.
- Nighthawk-side internal failures are represented by `nighthawk.step.failed`, and the span records exception + error status.
- There is no in-memory step trace API.

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

If you want a long-lived object, define it yourself and bind it as an ordinary Python value. Because expression evaluation and assignment operate on `step_locals`, bound values behave like any other local: they can be read via expressions and mutated in-place via `nh_eval`.

### 12.1. Carry pattern

The carry pattern is an idiomatic use of read bindings for cross-block context continuity. Pass a mutable object (e.g., `list[str]`) as a read binding (`<carry>`) and instruct the LLM to mutate it in-place via `nh_eval`. Read bindings prevent rebinding, so the caller's reference is preserved while the object contents are updated.

For practical examples and design tips, see [Patterns](patterns.md#cross-block-composition).

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
    - Raised when: `eval()` raises during `nh_eval` or `nh_assign` expression evaluation.
- `ToolValidationError(NighthawkError)`: Type validation/coercion failed during `nh_assign`.
    - Raised when: the assigned value does not match the expected binding type.
- `ToolRegistrationError(NighthawkError)`: Tool registration failed.
    - Raised when: a tool name is invalid, or a name conflict occurs without `overwrite=True`.

All exceptions are surfaced as Python exceptions and can be caught with standard `try`/`except`.

## 14. Step executor

### 14.1. Protocols

Nighthawk defines two step executor protocols. Both are `@runtime_checkable`.

- `SyncStepExecutor`
    - `run_step(*, processed_natural_program: str, step_context: StepContext, binding_names: list[str], allowed_step_kinds: tuple[str, ...]) -> tuple[StepOutcome, dict[str, object]]`
- `AsyncStepExecutor`
    - `run_step_async(*, processed_natural_program: str, step_context: StepContext, binding_names: list[str], allowed_step_kinds: tuple[str, ...]) -> tuple[StepOutcome, dict[str, object]]`

The type alias `StepExecutor = SyncStepExecutor | AsyncStepExecutor` is the union accepted by `nighthawk.run()`.

Both methods return a tuple of the step outcome and a mapping from binding names to their final values.

### 14.2. AgentStepExecutor

`AgentStepExecutor` is the built-in implementation that delegates Natural block execution to a Pydantic AI agent. It implements both `SyncStepExecutor` and `AsyncStepExecutor`.

Factory methods:

- `AgentStepExecutor.from_configuration(*, configuration: StepExecutorConfiguration) -> AgentStepExecutor`
    - Creates an executor with a managed agent built from the configuration.
- `AgentStepExecutor.from_agent(*, agent: StepExecutionAgent, configuration: StepExecutorConfiguration | None = None) -> AgentStepExecutor`
    - Creates an executor wrapping an existing agent. The agent is not managed (not rebuilt on configuration changes).
    - Configuration defaults to `StepExecutorConfiguration()` when not provided.

Instance attributes:

- `configuration: StepExecutorConfiguration` — the resolved configuration.
- `agent_is_managed: bool` — `True` when the agent was built internally from the configuration, `False` when provided externally via `from_agent`.
- `token_encoding` — tiktoken encoding resolved from the configuration.
- `tool_result_rendering_policy: ToolResultRenderingPolicy` — policy for rendering tool result previews, derived from configuration.

### 14.3. Custom backends

Any object implementing `SyncStepExecutor` or `AsyncStepExecutor` can serve as a backend. The protocol surface for `AsyncStepExecutor`:

```py
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_executor import AsyncStepExecutor, StepOutcome

class MyExecutor(AsyncStepExecutor):
    async def run_step_async(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        # processed_natural_program: the Natural program text after
        #   sentinel removal, dedent, f-string evaluation, and
        #   frontmatter stripping.
        # step_context: mutable per-step context containing step_locals,
        #   step_globals, and step_id.
        # binding_names: names declared as <:name> write bindings.
        # allowed_step_kinds: outcome kinds permitted for this block
        #   (e.g., ("pass", "return", "raise")).
        #
        # Return (outcome, binding_values) where binding_values maps
        # each committed binding name to its final value.
        ...
```

`SyncStepExecutor` follows the same shape with `run_step` instead of `run_step_async`.

For most custom backends, wrapping a Pydantic AI `Agent` via `AgentStepExecutor.from_agent` (see [Executors](executors.md#custom-backends)) is simpler than implementing the protocol directly. Direct implementation is appropriate when the backend does not use a Pydantic AI agent at all.

See the [API Reference](api.md#base) for the full protocol definition.
