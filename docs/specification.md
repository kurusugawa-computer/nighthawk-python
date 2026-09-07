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
    - Required fields include `execution_reference`, containing invocation identity and source location.
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
    - `inspect.unwrap(function)` follows the original function; signatures and original source remain conventional. `nh.get_transformed_function(function)` exposes the compiled body sharing the original module globals. It follows outer wrappers and supported method descriptors, requires no run, and raises `TypeError` for invalid inputs or wrapper cycles. Execute through the decorated function to establish runtime context.

### 5.2. Configuration

- `StepExecutorConfiguration`
    - `model`: Provider-qualified string or an existing Pydantic AI `Model` instance. Default: `openai-responses:gpt-5.6-luna` (see [Pydantic AI providers](pydantic-ai-providers.md)).
        - Examples: `openai-responses:gpt-5.6-luna`, `openai-responses:gpt-5.6-terra`.
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

Runtime Models are borrowed resources. Configuration validation, shallow derivation, managed Agent construction, and scope replacement preserve the exact supplied Model. Two instances with the same name can have different authentication. Nighthawk does not clone, reconstruct, mutate, enter, or close supplied clients. The host owns client lifetime and any SDK restrictions on threads or event loops.

A managed executor uses one common prompt, tools, dynamic output contract, capability, settings, and oversight path for either model form. Configuration model settings override Model defaults under Pydantic AI's existing precedence, with per-run capabilities retaining their ordinary behavior. Explicit tokenizer encoding wins; otherwise inference uses the string's model component or `Model.model_name`, falling back to `o200k_base` for unknown names. Invalid explicit encoding fails.

Model instances cannot be reconstructed from mappings. Configuration `repr` and `str` omit the model without invoking its representation. Python and JSON serialization including a live model field raises `PydanticSerializationError` with no traversal of the Model; explicit exclusion or inclusion of only other fields succeeds, including nested Pydantic records. String configurations remain serializable. A partial dump cannot reconstruct authenticated execution resources. `model_copy(update=...)` preserves Model identity by default but does not validate updates; callers must supply valid typed settings.

Hosts supply authenticated Providers and Models in memory. Nighthawk does not extract credentials or transport them through environment variables, operating-system process launch arguments, prompts, or its diagnostic payloads. Pydantic AI environment reads and existing provider-string authentication are supported. Docker credential delivery, arbitrary Model behavior, and external logging are host concerns. Forwarding settings such as `openai_store=False` proves request forwarding, not remote retention compliance.

External agents own model selection: both the constructor and `from_agent` reject instance-valued configuration models. Replacing their configuration with a different model string raises `NighthawkError` before scope installation. Keeping the configured string permits rendering changes. For managed agents, full configuration replacement selects its supplied Model and restores the exact parent executor on exit or error.

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
- `ExecutionReference`
    - Frozen dataclass representing runtime execution identity.
    - `run_id`: the Id of the outermost run (trace root).
    - `scope_id`: the Id of the current scope.
    - `step_execution_id`: a fresh Id for each runtime invocation, otherwise `None`.
    - `source_location`: the source label (`python_module:line`) during an invocation, otherwise `None`.

### 5.4. Runtime accessors

- `nighthawk.get_step_context() -> StepContext`
    - Get the `StepContext` for the currently executing Natural block. Raises if no step is active.

See [Section 10](#10-runtime-scoping) for additional runtime accessors (`get_step_executor`, `get_execution_reference`).

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
- If a read binding is missing or unbound, preparation fails with the original `NameError` or `UnboundLocalError` stored in `StepFailed.original_exception`. Unless terminal delivery supplies a host exception, the caller receives `ExecutionError` chained from that original exception (see [Section 10.4](#104-terminal-delivery-and-host-ledgers)).

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
    3. For each read binding (`<name>`), resolve the name using Python lexical rules (locals, enclosing cell scopes, name scopes, globals, builtins). Values resolved from locals, enclosing cell scopes, or name scopes are placed into `step_locals`. Values resolved from globals or builtins remain available through `step_globals` or its builtins namespace; they are not copied into `step_locals`. All resolved read bindings are included in `input_binding_name_to_value` for commit inspection and terminal records.
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
2. **Scoped tools** (`nighthawk.scope(tools=...)`): Declared callables are presented via the model's native tool-calling interface. Each tool definition adds a JSON Schema to every API request.

Binding functions incur no per-definition token overhead beyond the signature line in the prompt context. User-defined tools incur per-definition overhead proportional to the tool's JSON Schema size.

Design intent: Each parameter in a binding function signature represents a decision point the LLM must evaluate. The two-path design reflects this: binding functions carry minimal, LLM-friendly signatures while complex operations are composed in Python and exposed through simple binding functions. See [Natural blocks](natural-blocks.md#designing-binding-functions) for practical design patterns.

Tools are Python callables exposed to the LLM via pydantic-ai tool calling. There is no process-global tool registry: a tool is visible only inside a `nighthawk.scope(tools=...)` that declares it.

Declaration API:

- `nighthawk.scope(tools: Sequence[ToolEntry] | Extend[ToolEntry] | UnsetType = UNSET)` where `ToolEntry = Callable[..., Any] | pydantic_ai.tools.Tool[StepContext]`.
    - A plain callable is wrapped with `Tool(callable)`: the name is the function `__name__`, the description is the docstring, and a leading `RunContext[StepContext]` parameter is detected automatically.
    - A `Tool` instance is used as-is, allowing name, description, and metadata overrides.
- Tool names must be ASCII and match `^[A-Za-z_][A-Za-z0-9_]*$`.
- Ordinary sequences replace inherited tools; `tools=[]` leaves only built-ins. Omission or `UNSET` inherits; `None` is invalid. `Extend` appends.
- A repeated identical plain callable or exact explicit `Tool` object is idempotent, preserving the first normalized tool and its order. That normalized object can itself be supplied again. Different wrappers, or a plain callable versus a distinct explicit wrapper, conflict even if their function is identical.
- Names colliding with built-ins or distinct declarations raise `ToolNameConflictError`, which is both `ToolDeclarationError` and `NameConflictError`. Invalid entries or names raise `ToolDeclarationError`. Declaration checks never call user-defined equality. Tool and implicit-reference names are separate namespaces.
- Built-in tool names cannot be declared in any mode.
- `nighthawk.get_tools()` returns the scoped tools (excluding built-in tools) for the current scope.

Example:

```py
def get_step_execution_id(run_context: RunContext[StepContext]) -> str:
    """Return the current step Id."""
    return run_context.deps.execution_reference.step_execution_id or ""


with nighthawk.run(step_executor), nighthawk.scope(tools=[get_step_execution_id]):
    ...
```

The first parameter `run_context` is a Pydantic AI `RunContext[StepContext]` injected automatically by the framework. It is not exposed to the LLM as a tool argument.

Role split with implicit references:

- `implicit_references` injects Python names into `step_globals`; they render as signature lines and are invoked through `nh_eval`.
- `tools` exposes callables through native tool calling; they pass through the tool boundary (Section 8.3) and `Oversight.inspect_tool_call`, and are served to coding-agent backends over MCP.
- A given callable SHOULD be exposed through one of the two, not both.

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

Natural functions may be declared `async`. Tool execution and the final return expression have distinct awaitable-handling rules:

- Tool expression evaluation: if an `nh_eval` or `nh_assign` expression produces an awaitable, the host awaits it before returning the result to the model, including when the surrounding Natural function is synchronous.
- Return validation: if a `return` outcome's `return_expression` evaluates to an awaitable and the surrounding function is async, the host awaits it before return type validation. If the surrounding function is sync and the evaluated value is awaitable, execution fails with `ExecutionError`.
- Binding function calls: async binding functions called through `nh_eval` or `nh_assign` are auto-awaited in both sync and async Natural functions. The synchronous execution path bridges tool execution to an event loop; the awaitable-return restriction applies to the final `return_expression`, not to tool expressions.
- Concurrency: async natural functions are ordinary coroutines. Concurrent execution via `asyncio.gather` is safe for Natural blocks that do not share mutable bindings, since each block executes with an independent step context.

## 9. Return value

In the simplest docstring pattern, the Python function body returns a variable that is updated by execution:

- `return result`

If a step requests `outcome.kind == "return"`, generated Python returns the validated value after binding assignments and terminal delivery.

## 10. Runtime scoping

Nighthawk uses dynamic scoping carried via `contextvars.ContextVar`.

The required runtime object for step execution is:

- `step_executor` (required): a strategy object responsible for executing steps (Natural blocks).

Runtime execution identity is modeled separately in `ExecutionReference`:

- `run_id`: the Id of the outermost run (trace root). This serves as the golden thread that connects distributed agent processes (e.g. parent, child, grandchild) across process boundaries in observability tools.
- `scope_id`: the Id of the current (possibly nested) run scope. This serves as the identity of the current logical execution context.
- `step_execution_id`: a fresh Id for each runtime invocation, including repeated loop iterations and concurrent calls. Executor retries retain this Id; executing the block again allocates another.
- `source_location`: the source label (`python_module:line`), shared by invocations of the same block. Both step fields are `None` outside active step execution.

Nighthawk does not own workspace filesystem concerns (such as include resolution or host file operations). Those concerns belong to the host application layer that embeds Nighthawk.

Working directory selection for provider backends is configured via `ModelSettings["working_directory"]` (absolute, resolved). When empty (default `""`), backends omit the working-directory option and use the provider default (typically the parent process current working directory).
API:

- `nighthawk.run(step_executor: StepExecutor, *, run_id: str | None = None, usage_meter: UsageMeter | UnsetType = UNSET)`
    - Replaces the current context step executor with the provided step executor.
    - Installs `usage_meter` as the run-level meter when given; omission or `UNSET` creates a fresh `UsageMeter`; `None` is invalid.
    - Resets scoped tools and capabilities to empty, and oversight and lifecycle to `None`, for the run.
    - Generates a new `ExecutionReference` for the duration of the `with`.
    - Uses provided `run_id` when given; otherwise generates a new `run_id` (trace root).
    - Always generates a fresh `scope_id`.
    - Can be used even when no step executor is currently set.
- `nighthawk.scope(...) -> Iterator[StepExecutor]`
    - Every configurable parameter defaults to `UNSET`; omission and explicit `UNSET` inherit.
    - Ordinary values replace; empty sequences or mappings clear inherited collections.
    - `Extend(sequence)` appends system/user prompt suffix fragments, tools, or capabilities. Strings and bytes are invalid collection shapes. Repeated capability and fragment entries are preserved.
    - `Merge(mapping)` merges implicit references. Repeating the identical value is idempotent; a different value for the same name raises `NameConflictError` even if it compares equal. Wrappers capture collection structure without copying entries.
    - `oversight` and `lifecycle` accept `None`, clearing their hooks. Other fields reject it, as well as operation wrappers in unsupported positions.
    - Resolve every field before installing context; failures leave the parent identity and settings intact. Require a run, create a fresh scope Id, and restore the parent after normal or exceptional exit.
    - Executor replacement precedes full configuration replacement. Configuration updates require an `AgentStepExecutor` and respect managed/external ownership.
    - A replacement meter receives child usage without forwarding to the parent. Capabilities reach every Pydantic AI Agent request.
    - Yield the resolved executor. Nested runs reset scope defaults and restore their parent.
- `nighthawk.get_step_executor() -> StepExecutor`
    - Get the current step executor. Raises if unset.
- `nighthawk.get_execution_reference() -> ExecutionReference`
    - Get the current runtime execution identity. Raises if unset.

All run-scoped public getters, including `get_usage_meter()`, `get_oversight()`, and `get_lifecycle()`, require an active run and raise `NighthawkError` outside it. The meter is non-optional; oversight and lifecycle may be `None` inside a run. `get_step_context()` requires an active step, so a run alone is insufficient. Private optional meter discovery preserves budget behavior outside runs.

### 10.1. Observability contract (OpenTelemetry span/event)

Nighthawk uses OpenTelemetry spans as the sole runtime trace model.

Runtime spans:

- `nighthawk.run`
- `nighthawk.scope`
- `nighthawk.step`

Identity attributes:

- `run.id: str`
- `scope.id: str`
- `step.execution.id: str` (fresh invocation Id, on `nighthawk.step`)
- `step.source_location: str` (exact format: `python_module:line`, on `nighthawk.step`)

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

### 10.2. Host commit boundary

The executor/model contract remains the expression-based `StepOutcome`. Before inspection, the runner checks allowed kinds, validates writes, and evaluates and validates a return expression once, including awaiting it once for async functions. Invalid initial results fail before inspection. An initial raise has an empty output mapping, never unvalidated writes. Its exception type is resolved and validated before inspection; a missing name or a value that is not an exception class fails with `ExecutionError` before the hook runs.

`StepCommit.outcome` is a resolved `StepResult`: frozen `Pass()`, `Return(value)`, `Break()`, `Continue()`, or `Raise(message, error_type=None)` records exported through `nh.oversight`. Each has a fixed non-init `kind`. Only `Return` has a value; `Return(None)` is distinct from `Pass()`. Raise's optional error type is a Python binding name resolved by existing exception rules. The internal runner envelope and generated return/break/continue dispatch use the same resolved variants.

The hook runs once. `Accept` retains validated values without repeating validators. `Reject` produces a `StepFailed` at `oversight_rejection` before final bindings are assigned; its original exception is `OversightRejectedError`. `Rewrite` fields `outcome`, `binding_name_to_value`, and `return_value` default to `UNSET`. A supplied mapping replaces all committed writes, including an empty mapping. Empty rewrites, invalid outcome/mapping shapes, or both outcome and return_value are errors. `Rewrite(return_value=None)` explicitly supplies a return subject to annotation validation; `UNSET` cannot represent an application return through this patch field.

A return_value patch requires an existing Return. Changing another allowed kind to return uses `Rewrite(outcome=Return(value=...))`. Rewrites recheck allowed kinds and validate supplied replacements before any final assignment, without a second inspection. Bindings-only rewrites retain the resolved return and never replay its expression. Undeclared output names are rejected. A final Raise commits no writes; a nonempty replacement mapping paired with Raise is invalid. Rewriting away from an initial raise inherits an empty mapping unless replacements are supplied, so unvalidated output cannot reappear.

An unchanged Raise reuses its resolved exception type; a replacement Raise resolves and validates its own type. The exception instance is constructed only for the final Raise, after inspection. Rejecting a candidate or rewriting it to another kind does not execute the original exception constructor.

`StepCommit` and `ToolCall` capture top-level mapping membership in shallow read-only views. Contained lists, models, and arbitrary objects retain type and identity; frozen records do not deeply isolate application objects. Trusted hooks must use Rewrite rather than mutate these references. Views support synchronous inspection; durable history requires host copying or serialization. Logical commit guarantees cannot roll back tool, validator, or return-expression side effects.

### 10.3. Return expression approval

`Oversight.inspect_return_expression(ReturnExpression) -> Accept | Reject` runs after outcome admission and initial write validation, before compilation, evaluation, or await. The request carries `execution_reference`, expression text, `expected_type`, `processed_natural_program`, and `validated_binding_name_to_value` in a shallow read-only view. It cannot rewrite the expression or provide an evaluator. Invalid decisions fail at `return_inspection`; explicit rejection records `oversight_rejection`, `inspection_subject="return_expression"`, and the reason.

Core owns expression evaluation, await in async functions, and return validation exactly once for the original candidate. The later resolved commit inspector still supports replacement values, which receive their own validation. An absent expression inspector preserves permitted return support; `deny: [return]` prevents both inspection and evaluation. This approves trusted execution and is not a sandbox. Hosts whose value policy permits only write bindings and capability requests must continue denying return expressions.

### 10.4. Terminal delivery and host ledgers

`nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=callback))` installs a synchronous callback accepting `StepFinished` and returning `None`. Omission or `UNSET` inherits; `None` clears. A new run resets lifecycle. Each execution captures the configuration on entry, before preparation, and attempts notification once while Python can unwind normally. Definition-time parsing, decorator compilation, and failure to enter a required run are outside this boundary. Identity, step context when established, and the step span remain active during delivery and restore afterward, including when delivery fails.

Generated Python resolves the candidate, performs each selected write assignment, records each completed assignment, delivers the terminal record, and then dispatches return/break/continue. A prepared DSL exception is dispatched after delivery as well. Callback failure prevents further control dispatch but does not undo completed assignments, object mutation, tools, validators, or expression side effects. Partial assignment failures report only writes whose assignments completed. Rejected commits perform no generated assignments.

`StepFinished` is the discriminated union of frozen `StepCompleted`, `StepRaised`, `StepFailed`, and `StepInterrupted`, with respective `kind` values `completed`, `raised`, `failed`, and `interrupted`. All carry `execution_reference`, `processed_natural_program`, `input_binding_name_to_value`, `allowed_step_kinds`, and `assigned_binding_name_to_value`. Preparation-dependent fields are `None` when not established; empty mappings or tuples mean known empty values. Runtime references require both step identity fields. Mapping and tuple structure is snapshotted; contained Python objects retain their exact types and identity. Hosts must serialize or copy deliberately for durable records.

`StepCompleted.outcome` is the final Pass/Return/Break/Continue. `StepRaised` holds a final Raise and its successfully constructed `exception`, including domain exceptions subclassing NighthawkError. Invalid exception names fail at `raise_resolution`; exception constructor failures are `StepFailed` at `raise_construction`, not domain raises. `StepFailed` holds `original_exception`, `failure_stage`, optional `attempted_outcome`, and optional `validated_binding_name_to_value`. Explicit rejection additionally carries `inspection_subject` and `rejection_reason`. Invalid hook decisions remain inspection failures.

`FailureStage` identifies preparation, executor, outcome_validation, binding_validation, return_inspection, return_evaluation, return_await, return_validation, commit_inspection, rewrite_validation, oversight_rejection, raise_resolution, raise_construction, or binding_assignment. Classification uses structured runtime state, not exception text. Ordinary execution exceptions propagate as `ExecutionError(step_failed)` chained from the record's original exception unless delivery supplies a host exception. DSL Raise propagates its prepared exception.

The callback may append the record to a host-owned authoritative ledger and raise a public host exception containing that exact stored event. For StepFailed, core explicitly chains the callback exception from `original_exception`; for StepRaised, from the prepared domain exception. Arbitrary callback exceptions are preserved and never reclassified or redelivered. `StepDeliveryError(execution_reference, cause)` is available for host adapters reporting storage failure; its `delivery_cause` does not imply a durable event exists. During ordinary execution failure, the outward chain prioritizes the execution exception and the storage cause remains available in `delivery_cause`.

`StepInterrupted` holds the active `failure_stage` and original cancellation or process-control `BaseException`. Delivery is attempted synchronously without an additional await. If delivery also fails, the original interruption still propagates, explicitly chained from the delivery exception. Cancellation, SystemExit, and KeyboardInterrupt are not translated into ordinary host execution failures.

The guarantee is one in-process delivery attempt, not a transaction across Python and storage. A callback that appends and then raises cannot be distinguished automatically from an uncertain append failure. Hosts deduplicate and retry storage by `step_execution_id`, never by replaying a Natural execution. Hard process termination and unavailable storage cannot guarantee durable delivery. OpenTelemetry is optional diagnostics, not the authoritative ledger. Terminal tracing follows the typed classification: await failures receive `nighthawk.step.failed` and exception/error status; interruptions receive `nighthawk.step.interrupted`; delivery failures may add `nighthawk.step.delivery_failed` without a second execution terminal event.

Host integrations can replace pending-commit correlation with the shared `execution_reference` and replace type-erasing validation workarounds with terminal records carrying already validated values. No external host code or host-specific serialization schema is provided by core. The deterministic ledger fixture in `tests/governance/test_step_ledger.py` demonstrates recording even when Python inside the Natural function catches the translated host exception.

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

Nighthawk defines public library errors rooted at `NighthawkError`, plus an internal `ToolBoundaryError` signal for recoverable tool failures.

Exception hierarchy:

- `NighthawkError`: Base class for public Nighthawk library errors.
    - Raised when: runtime preconditions fail (e.g. no active run context, missing step executor).
- `NaturalParseError(NighthawkError)`: Natural block parsing or frontmatter parsing failed.
    - Raised when: the sentinel is missing, bindings are invalid, frontmatter YAML is malformed, or AST extraction fails.
- `ExecutionError(NighthawkError)`: runtime internal failure; `step_failed` holds its `StepFailed` record and `__cause__` holds the original exception. A DSL Raise without an explicit type may also construct this exception with a message and no failure record.
    - Raised when: the LLM returns invalid JSON, an outcome kind is disallowed, return value validation fails, or `raise` outcome is triggered without a matching exception type.
- `ToolEvaluationError(NighthawkError)`: Low-level expression evaluation helper failure. Built-in tool boundaries convert this into a structured failure observation.
- `ToolValidationError(NighthawkError)`: Exported exception type; the built-in `nh_assign` implementation does not raise it.
- `ToolBoundaryError(Exception)`: Internal tool boundary signal with an `ErrorKind` and optional guidance. A failed `nh_assign` value validation raises this signal with `kind="invalid_input"`; the tool wrapper converts it into `ToolOutcome.error` for the model. It is not a `NighthawkError` subclass.
- `ToolDeclarationError(NighthawkError)`: Invalid tool declaration.
- `NameConflictError(NighthawkError)`: Distinct declarations claim the same name.
- `ToolNameConflictError(ToolDeclarationError, NameConflictError)`: Tool name collision, including reserved built-in names.
    - Raised when: a tool name is invalid, collides with a built-in tool, or is declared twice within the visible scope.

Exceptions propagated to the host can be caught with standard `try`/`except`. Recoverable tool failures are instead returned to the model as structured observations, as specified in [Section 8.3](#83-tools-available-to-the-llm). Internal step failures follow the `ExecutionError` and terminal-delivery rules in [Section 10.4](#104-terminal-delivery-and-host-ledgers).

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
        #   step_globals, and execution_reference.
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
