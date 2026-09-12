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

Execute natural-language descriptions of work inside ordinary Python programs, from small classifications to autonomous agent tasks.
Python owns deterministic procedure, execution points, permitted control results, and declared names receiving validated writes.
Natural supplies judgment using accessible lexical values, inherited parent working state, and scoped task references.
Read bindings express dependencies and presentation priority, not an access allowlist.
Essential inputs should normally be explicit parameters; inherited task policy trades less argument plumbing for contextual dependencies.

Ordinary applications need `natural_function`, output contracts, and an executor selected through `run`; `scope` varies configuration and task injection.
Host approval and lifecycle records are optional integration surfaces, while runtime validation is mandatory for every executor.
Use native Pydantic and Pydantic AI value contracts, models, agents, tools, content, capabilities, and usage types where they fit.
Source instrumentation enters the runtime and dispatches results; Natural prose remains interpreted work rather than generated deterministic Python source.

| Component | Responsibility |
| --- | --- |
| Python application | Calls, lexical state, deterministic control flow, domain types, and task decomposition. |
| Generic Agent host, when used | Task history, concrete domain operations, approval, durable records, and retry or compensation policy. |
| Natural runtime | Environments, operation admission and execution, validation, control results, and lifecycle notifications. |
| Pydantic / Pydantic AI | Value contracts, model and tool interaction, content, capabilities, and usage. |
| Backend adapter | Transport and model-facing translation through the common runtime boundaries. |
| Context builder | Bounded presentation of accessible state, without changing lookup or authority. |

## 2. Non-goals

- Sandboxing hostile generated Python; isolation belongs to the host or backend.
- Compiling Natural prose into deterministic Python.
- Durable suspended Python execution, workspace reconstruction across restarts, or automatic operation replay.
- Automatic background scheduling or joining of child tasks.
- A full skills framework; coding backends use their native skill systems.
- Executing Python code blocks embedded in markdown as a separate nesting mechanism.

A block is not a transaction.
Object mutations, helper calls, validators, and external operations may have effects before finalization; rejection prevents pending name assignments but cannot undo those effects.
Frozen records and shallow read-only mappings protect structure, not the referenced object graph.
Each block starts a fresh model interaction; inherited working values do not include a parent's model message transcript.

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
- Step locals (`step_locals`): captured working values and tracked assignment bookkeeping; the environment owns full expression lookup.
- Step environment (`StepEnvironment`): provenance-preserving Python, parent, task, and step views with one effective lookup contract.
- Step globals (`step_globals`): internal namespace storage; it is not an independent lookup authority.
- StepContext: a mutable, per-step object (one Natural block execution) passed to tools and executors.
    - Required fields include `execution_reference`, containing invocation identity and source location.
    - Model selection and prompt policy are owned by `StepExecutorConfiguration`; StepContext does not carry model configuration.
- Locals summary: a bounded presentation of selected working values and their provenance, included in the LLM prompt.
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
    - Instrumentation preserves original defaults without evaluating them again, live closure cells, and defining module globals. Generated helper names avoid application bindings, including names appearing inside Natural text.

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
    - `parent_step_execution_id`: the active parent step Id, or `None`.
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

- `<name>`: read binding. Resolve the current value of `name` in the effective environment and prioritize its presentation to the LLM.
- `<:name>`: output binding. Authorize a pending Python name assignment at the block boundary.

Resolution note:

- Read bindings use the same environment as runtime operations: step assignments, Python lexical names, inherited working state, then scoped task references.
- An unbound Python local or closure cell blocks fallback to a same-name parent or task value.
- If a read binding is missing or unbound, preparation fails with the original `NameError` or `UnboundLocalError` stored in `StepFailed.original_exception`. Unless terminal delivery supplies a host exception, the caller receives `ExecutionError` chained from that original exception (see [Section 10.4](#104-terminal-delivery-and-host-ledgers)).

Constraints:

- `name` is a simple identifier (no dotted paths).
- `<:name>` does not require prior declaration.
    - Practical note: if subsequent Python code reads a variable that has not been assigned yet, Python will raise before any LLM behavior can help. Initialize variables in Python when needed.

Type note:

- Nighthawk captures source annotations and resolves required output and return contracts during step preparation.
- An explicit annotation, including `object`, takes precedence over the initial value's type.
- A variadic parameter's annotation constrains its elements: `*values: int` selects `tuple[int, ...]` and `**values: int` selects `dict[str, int]` for the local output binding. `Unpack` and starred tuple annotations already specify the collection contract.
- Presentation-only annotation metadata is not evaluated to enrich a prompt or discovery response.
- On Python 3.14, deferred callable annotations use bounded source-file acquisition when available; their spelling is shown without invoking `__annotate__`. Missing source and potentially modified deferred metadata are explicit limitations. Explicitly stored annotation dictionaries remain usable.
- A global output can require the module's deferred annotation evaluator during preparation. Failure is a preparation error; it does not silently weaken the selected contract. This evaluator is not called merely to render unrelated module annotations.

Annotation acquisition:

| Annotation source | Required acquisition |
| --- | --- |
| Decorated function parameters and local declarations | Capture annotations from the function's source, including declarations inside compound statements, excluding nested function and class scopes. A single declaration for an output establishes its static annotation; reject ambiguous multiple declarations rather than guessing from branch execution. |
| A `global` output | Use the defining module's annotation metadata when present; no module-source reconstruction is required. Otherwise use initial-value inference. |
| A `nonlocal` output | Preserve the cell and writeback destination. Recovering an enclosing function's local annotation is outside the guarantee; use initial-value inference without scanning outer source or caller frames. |

Required annotations, including the Natural return contract, resolve during preparation with `Annotated` metadata retained.
Resolution failure is a preparation error; presentation-only metadata remains best-effort.
Freeze contracts at entry, including explicit `object`, `Any`, and `None`.
Ordinary Python assignments and helper bodies are not intercepted for validation.

Runtime type inference:

- A missing annotation has its own internal marker, distinct from an explicit `object` annotation.
- At step entry, an unannotated initialized output uses the initial value's Python type (e.g., `result = ""` infers `str`).
- An uninitialized output, `None`, or a generic `object()` instance uses `object` as its fallback contract.
- This enables `nh_assign` type validation and retry for unannotated bindings that have typed initial values.

Clarifying note (bindings vs tool targets):

- Bindings (`<name>`, `<:name>`) are always simple identifiers.
- Tool targets (for example `nh_assign`) may use dotted paths for attribute mutation.
- Commit selection remains based on `<:name>` identifiers (top-level names only).

## 8. Runner model

### 8.1. State layers: python locals and step locals

`StepContext.environment` owns effective lookup and separate read-only views of Python, parent, task, and step bindings.
Lookup prefers tracked step assignments, then Python locals/cells/globals/builtins, then inherited working state, then scoped task references.
Python locals are captured from the instrumented function at block entry; closure cells and defining module globals remain live.
Unbound locals and cells retain Python's blocking semantics.
Unrelated caller frames are not lexical dependencies.

A child captures its parent's materialized working associations, including inherited state, current Python working locals and cells, and tracked assignments.
It does not inherit the parent's module globals, task configuration as locals, generated runtime bindings, or model transcript.
The child's own scoped task references remain separately available.
Parent membership and object associations are snapshots; referenced mutable objects remain shared.

`step_locals` holds captured Python working values and tracked assignment bookkeeping; it is not the complete effective environment.
Use `environment.resolve`, its source views, or the common runtime operations for name lookup.
Generated assignments write validated, selected `<:name>` values back to the corresponding Python locals, cells, or globals after finalization.
All initialized declared outputs are considered, even without tracked assignment or mutation marks.
Every block starts a fresh model interaction by default; Python values and inherited working state carry context between blocks.

#### 8.1.1. Environment views and lookup

All environment views have string keys and shallow read-only structure.

| Member | Meaning |
| --- | --- |
| `python_name_to_value` | Values accessible through the block's Python lexical environment, including enclosing cells, globals, and builtins. |
| `parent_name_to_value` | A snapshot of inherited parent working state, with the nearest parent taking precedence. |
| `task_name_to_value` | The resolved mapping injected by the active scope. |
| `step_name_to_value` | Values explicitly assigned in this step, including pending declared outputs and temporary working names. |
| `resolve(name, *, source="effective")` | Resolve a name from the effective environment or specifically from `python`, `parent`, `task`, or `step`. |
| `describe(name)` | Describe the effective origin, shadowed alternatives, write eligibility, captured annotation, selected output contract, and usable references without evaluating the value's properties. |

Effective lookup uses tracked step assignments, Python locals, enclosing cells, globals, builtins, nearest inherited parent state, then task references.
A Python binding with value `None` is present; an unbound local or cell blocks fallback with its Python lookup error.
Absence everywhere raises `NameError`.
Step temporaries may shadow parent or task names, but an existing Python name can be rebound only as a declared output.
Select a shadowed value with `environment.resolve(name, source="parent")` or `source="task"`; selection applies to that value reference, not an entire expression's namespace.
Bindings, evaluation, assignment expressions, model-selected return and exception expressions, and discovery share this resolution contract.

#### 8.1.2. Supported Python source forms

The baseline is source-available ordinary `def` and `async def` functions on supported CPython 3.13+ versions.

| Source form | Contract |
| --- | --- |
| Parameters and ordinary function locals, including `a: int = 0` and uninitialized `a: int` | Required: preserve local classification; obtain declared output annotations from source and distinguish an unbound name from an absent one. |
| Module globals and builtins | Required: resolve through the defining function's live module environment, including later rebinding. |
| Existing Python closure cells | Required: retain the original cells, observe later rebinding, and report an empty cell without falling back to parent or task state. |
| `<:name>` with a valid Python `global name` declaration | Required: validate the declared output and write back to the defining module at finalization. |
| `<:name>` with a valid Python `nonlocal name` declaration | Required: validate the declared output and write back to the existing enclosing cell at finalization. |
| An outer function's variable mentioned only in Natural text and absent from the decorated function's original closure | Outside the guaranteed contract: do not create a stale decorator-time capture. Use an explicit parameter, a valid `nonlocal` declaration establishing a cell, or task injection. |
| Unavailable function source, arbitrary caller-frame reconstruction, or an unverified interpreter implementation | Outside the required baseline; report unsupported source/interpreter forms explicitly. |

Natural text alone does not establish a Python closure, and arbitrary caller-frame reconstruction is unsupported.
Use explicit inputs, a valid `nonlocal` declaration establishing a cell, or task injection for otherwise inaccessible outer values.
Lexical value access does not imply recovery of an enclosing source annotation; Section 7 bounds annotation acquisition separately.

#### 8.1.3. Parent lifetime and expression lifetime

Parent inheritance is guaranteed for child calls whose completion the parent waits for, synchronously or with `await`, directly or through a Python helper.
A helper can run children concurrently if it joins them before returning.
Each child snapshots associations at its own entry; later parent rebinding does not change that snapshot, while shared-object mutation remains visible.
Child temporary rebinding does not write back to its parent, and siblings have separate workspaces.
Cleared task injection cannot reappear as copied parent task configuration; an actual materialized parent temporary remains parent state.

Children that outlive their parent step are outside this inheritance guarantee.
Copied context variables do not extend a finished step's lifetime; hosts establish explicit inputs and execution scope for independent work.
Supported thread bridges propagate context; separate processes do not inherit live objects through an execution Id.

Within one operation, immediately invoked lambdas and consumed generators use effective lookup for surrounding names and ordinary Python parameter/iteration scopes.
Retaining a generated lambda or generator for a later operation, even within the same step, has no guaranteed capture or rebinding semantics.
Taking one outside its creating step also has no guaranteed environment lifetime.
Original application callables retain their own Python closures and live globals; the Natural environment applies to the operation expression, not arbitrary called function bodies.

### 8.2. Prompt context

To reduce black-box behavior, Nighthawk includes bounded prompt context sections in the user prompt.

#### 8.2.1. User prompt structure

The default user prompt template renders three delimited sections:

- `<<<NH:PROGRAM>>>` / `<<<NH:END_PROGRAM>>>`: the Natural program text (after sentinel removal, `textwrap.dedent`, and f-string evaluation when applicable).
- `<<<NH:LOCALS>>>` / `<<<NH:END_LOCALS>>>`: the locals summary (see 8.2.2).
- `<<<NH:GLOBALS>>>` / `<<<NH:END_GLOBALS>>>`: the globals summary (see 8.2.3).

The template uses `$program`, `$locals`, and `$globals` placeholders, substituted at prompt construction time.
A bounded runtime-access preamble precedes the template.
It identifies the collision-free inspection and environment references, and relevant known source collisions.

#### 8.2.2. Locals summary

The locals summary presents current Python working locals, inherited working state, and step assignments.
Each entry identifies its origin and available declaration annotation or selected output contract.
These descriptions do not change the live environment or its validation contracts.

Selection:

- Python working locals, parent working names, step assignments, and declared outputs are eligible, including application names starting with `__`.
  Other available names remain retrievable through environment discovery.
- Instrumentation allocates internal names that avoid application bindings and names present in the function source, including Natural text.
  Only the internal bindings actually used by that transformed function are excluded from its Python environment, initial context, and inherited working state.
  Application names are not excluded by a shared prefix; their lexical priority, unbound status, and explicit source references remain intact.
  The separate prohibition on `__` segments in assignment-tool targets still applies (Section 8.3).

Ordering:

- Explicit references and outputs precede other Python working locals, which precede inherited state.
  Ordering within a priority class is lexicographic.

Rendering format:

- `TypeAliasType` values (PEP 695) retain their name and explicitly omit expansion that would require evaluation.
- Callable values use static, best-effort signature metadata shared with value inspection.
  Available annotations are included, such as `(base: int, bonus: int) -> int`; unresolved annotation spelling is preserved without evaluation.
    - If the callable has a meaningful docstring, the first line is appended as `# first_line`.
    - If the callable is async, `async` is appended in metadata comments.
    - If multiple callable entries share the same signature text, each is annotated with `# disambiguation: use name`.
    - If signature acquisition requires executing an application descriptor, the entry renders as `name: <callable; signature-unavailable>` without reading that descriptor.
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

- Top-level entries follow the priority classes above and use lexicographic order within each class.
- Object methods and fields are rendered in lexicographic order by member name.
- Methods are rendered before fields for each object entry.
- If a section-level budget prevents rendering all top-level entries, `<snipped>` is appended at the end of the section.

Safety:

- Prompt rendering does not call user methods.
- Prompt rendering does not evaluate application properties, annotation expressions, serializers, or `repr` methods.
  Known functions, ordinary instance dictionaries, standard slot storage, and supported native content supply static metadata; other values remain explicitly opaque.
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
- Tight item or token budgets can omit even explicit references.
  Omission notices identify omitted names and retain a usable environment-discovery reference.

#### 8.2.3. Globals summary

The globals summary presents referenced Python global or builtin names and injected task capabilities that are not already represented in the working-state summary.

Reference extraction:

- The Natural program text is scanned for unescaped `<name>` tokens (both read bindings `<name>` and dotted references `<name.field>`).
- For dotted references, the top-level name (before the first `.`) participates in globals selection, and the full dotted path remains available for multimodal leaf rendering.
- Escaped references (`\<name>`) are not extracted. The backslash is removed in the program text passed to the model.

Selection:

- A referenced name is included here when it is not already represented in the working-state summary.
- The name is resolved through `StepEnvironment`; task injection never replaces Python name resolution.
- If resolution fails, the name is silently omitted.

Ordering:

- Explicit references precede other task-capability names, with lexicographic ordering within a priority class.

Rendering format:

- Same rules as the locals summary (Section 8.2.2).

Truncation:

- Rendering is bounded by `context_limits.globals_max_tokens` and `context_limits.globals_max_items`.
- Truncation behavior is the same as the locals summary.


#### 8.2.4. Replaceable context construction

`scope(context_builder=builder)` replaces context construction for managed and externally supplied Pydantic AI agents in that scope.
Omission or `UNSET` inherits, nested scopes restore the parent on exit, and a new run starts with `DefaultContextBuilder`.
An invalid builder is rejected before any scope state changes.

`ContextBuilder.build(request: ContextRequest) -> Sequence[UserContent]` is synchronous.
`UserContent` is the native Pydantic AI type; asynchronous retrieval belongs in tools.
The request supplies `execution_reference`, `processed_natural_program`, `step_context`, ordered `explicit_reference_names` and `explicit_reference_paths`, `output_binding_names`, `binding_name_to_type`, `return_annotation`, and `configuration`.
`environment` exposes the same live environment used by operations, and `context_limits` exposes the configured budgets.
Output contracts have shallow read-only mapping structure.

A builder may prioritize, omit, or summarize presentation without changing runtime lookup, object identity, or final validation.
It returns a sequence rather than a scalar string, bytes, or serialized record.
Native multimodal items and cache points retain identity through this boundary.
Configured and scoped user-prompt suffix fragments are appended by common code once after builder output; builders should not add them again.
`DefaultContextBuilder.build(request)` can be composed inside a custom builder and uses the presentation described above.
Builder exceptions and invalid content fail the step before model execution and receive the ordinary terminal lifecycle notification.

#### 8.2.5. Value inspection and name discovery

`nighthawk.runtime.inspect_value(value, cursor=None, max_items=8)` describes a known live value.
It does not infer a binding name, source, write eligibility, or declared contract from object identity.
Callable annotations belong to the value; named declaration annotations and output contracts belong to `StepEnvironment.describe(name, source="effective")`.
Presentation-only annotation spelling is not evaluated and does not weaken preparation-time output validation.

The default context advertises `environment.runtime_reference`, a generated collision-free Python root.
Its `.inspect(value)` method uses the same value-description implementation, and `.environment` exposes the current environment.
Application bindings named `inspect`, including unbound Python locals, retain their meaning.
The generated root cannot be used as a temporary assignment target and is excluded from inherited working state.

`StepEnvironment.reference(name, source="effective")` returns an expression that resolves the binding when evaluated.
`describe` provides origin, shadowed sources, unbound state, write eligibility, annotation spelling, output-contract text, and source-qualified references.
`discover(source="effective", cursor=None, max_items=8)` retrieves names with these descriptions, even when the values themselves are opaque.
Sources are `effective`, `python`, `parent`, `task`, and `step`.

Supported name and member listings return a `next_cursor` and an actionable continuation when truncated by their item limit.
Pages progress for stable name membership; changed membership or an invalid cursor returns a concrete instruction to restart discovery.
When a discovery page exceeds the tool-response budget, rendering reduces the page and preserves complete reference and continuation expressions.
Metadata may be omitted explicitly to retain those expressions.
If even one reference and its continuation cannot fit, the response explains the limit and recommends Python slices on environment views or a larger response budget; it does not return a truncated cursor.
Discovery pages nested in builtin dictionaries, lists, and tuples receive the same protection and share the whole value's response budget.
If the surrounding data cannot fit alongside the pages, the response recommends evaluating discovery separately or increasing the budget.
Recovery records are complete JSON; extremely small budgets may retain only a shorter recovery record or `{}`.
Composing discovery with native media preserves the native content objects when the composed value fits.
Descriptors and unavailable metadata are identified as omissions, without promising that another page can execute them.
Large collections can be examined with ordinary Python slices.

An inspection or environment-helper call inside an evaluation expression belongs to that one operation.
Direct Python calls to these helpers do not create an additional Natural operation, approval, or completion notification.

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
- Names colliding with built-ins or distinct declarations raise `ToolNameConflictError`, which is both `ToolDeclarationError` and `NameConflictError`. Invalid entries or names raise `ToolDeclarationError`. Declaration checks never call user-defined equality. Tool names and task-reference names are separate namespaces.
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

Role split with task references:

- `task_references` supplies a distinct `StepEnvironment.task_name_to_value` mapping without modifying Python globals.
  Effective lookup prefers step writes, then Python names, inherited working state, and task references.
  Explicit source selection can retrieve a shadowed task value.
- `tools` exposes callables through native tool calling; they pass through the tool boundary (Section 8.3) and `Oversight.inspect_operation`, and are served to coding-agent backends over MCP.
- A given callable SHOULD be exposed through one of the two, not both.

Provided tools (built-in):

- Provided tools are always available by default.
- Provided tools are exposed with names prefixed by `nh_` to reduce collisions.

Tools resolve names through `StepEnvironment`, using the precedence and source boundaries in Section 8.1.
Expression evaluation uses one live namespace for both globals and locals, so immediately executed comprehensions, generators, and lambdas see the same environment.
Deferred execution outside the owning step is not a workspace-lifetime guarantee.

Eval tool:

- `nh_eval(expression: str) -> object`
    - Evaluate a Python expression and return the result. Use to inspect values, call functions, and mutate objects in-place.
    - In-place mutations performed via `nh_eval` are not runtime-validated.
    - If the evaluated expression is awaitable, it is awaited before returning.
    - Named assignment expressions such as `(value := 8)` are rejected; use `nh_assign` for tracked name assignment.

Binding tool:

- `nh_assign(target_path: str, expression: str) -> object`

Target grammar:

- `target_path := name ("." field)*`
- `name` and `field` are ASCII Python identifiers.

Reserved targets:

- Any segment starting with `__` (dunder) is disallowed.

Semantics of `nh_assign`:

- Admit target grammar, root eligibility, and known missing/unbound attribute roots before inspection or expression evaluation, without invoking application getters.
- Subscripts, calls, destructuring, and mapping-key fallback are not assignment targets.
- To mutate an explicitly selected parent or task object, retain it under an eligible temporary name before using an attribute path.
- Evaluate `expression` once in the effective environment, awaiting its result when needed.
- If `target_path` is a bare `name`:
    - A declared output validates against its entry-time contract before updating the tracked working value.
    - An existing Python name requires an output declaration, including when the Python name is currently unbound.
    - A new temporary name may shadow parent or task state; it receives no Python writeback without an output declaration.
    - The generated environment helper root is protected from assignment.
- If `target_path` is dotted (`name.field...`):
    - After right-hand-side evaluation, resolve the live root and intermediate attributes once.
    - The final target must be an ordinary declared Pydantic field on a model with `validate_assignment=True` and standard assignment machinery.
    - Invoke Pydantic's model assignment once; do not validate a detached field or run a second setter.
    - Preserve field metadata, constraints, model and field validators, configuration, and target identity; frozen fields/models reject writes.
    - Do not read the final attribute merely to infer its contract or probe its setter.
    - The right-hand side may change which live object is subsequently selected; a later traversal or validation failure retains earlier effects.
    - Properties, custom assignment machinery, and other attribute targets return a recoverable unsupported-target observation recommending an ordinary Python helper.

Commit and mutation notes:

- Commit selection is controlled only by `<:name>` bindings.
- `<:name>` selects which top-level names are committed from `step_locals` into Python locals at Natural block boundaries.
- Tracked assignment and mutation marks support working-state bookkeeping; they do not select which initialized outputs receive final validation.
- All initialized declared outputs receive final validation, with explicit candidate proposals taking precedence; an initial `Raise` discards pending writes.
- Mutation of a shared object is immediately visible and does not require a generated name assignment.
- Objects outside declared outputs are not recursively validated at finalization.

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
    - Argument validation failures and explicit `ModelRetry` use the dedicated retry-prompt path (for example via `RetryPromptPart.model_response()`).
      Ordinary application exceptions propagate to Python.
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

- Runtime input and name-resolution failures provide recoverable observations, as specified in [operation inspection](#103-operation-inspection).
- Ordinary application exceptions and host oversight failures propagate toward the Python caller; a transport MUST NOT replace them with generic retry advice.
- Native tool dispatch uses Pydantic AI's argument validators, capability hooks, and approval requirements.
  Actual argument validation failures and explicit `ModelRetry` remain recoverable; a validator's propagated application exception or an exception raised by the tool body retains its original meaning.

Coding-agent tool callbacks execute on the event loop that owns the backend request, including when the MCP HTTP server runs in another thread.
Callbacks preserve the captured Natural scope and serialize in transport admission order.
A failed callback stops the backend request even if its client converts the callback exception into a protocol error response.
Request cancellation cancels pending callbacks and cleans up the coding-agent process.

The projected `value` field is bounded by `context_limits.tool_result_max_tokens` and may be summarized using [headson](https://github.com/kantord/headson) truncation when the full rendering exceeds the token budget. Headson is a structure-aware JSON summarizer: it parses the full JSON tree, then selects representative nodes to produce a compact preview that preserves the shape and key values of the data within a strict byte budget (analogous to `head`/`tail` but for structured data).

Partial effects:

- Static admission failure does not evaluate the right-hand side.
- A failed pending name assignment retains its prior working value, but earlier helper, validator, or object-mutation effects remain.
- Dotted traversal or model validation can fail after right-hand-side effects; the block provides no transaction or automatic rollback.

#### 8.3.1. Supporting types (internal)

- `ToolBoundaryError`: Exception carrying `kind` (ErrorKind), `message`, and optional `guidance`. Raised by tool implementations to signal structured failures.
- `ToolResultRenderingPolicy`: Frozen dataclass controlling how tool result previews are rendered (tokenizer encoding name, max tokens, JSON renderer style).

### 8.4. Execution contract (final JSON)

The managed model adapter parses a final JSON object representing one outcome variant.
This transport schema belongs to the model adapter; custom executors return resolved `StepCandidate` values as described in Section 14.

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
    - `return_expression` is evaluated through the common operation pipeline against the step environment before the adapter returns a candidate.
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
    - The adapter resolves the exception class through the common operation pipeline. Without a supplied class, it uses `Exception`.
    - The runner constructs the final exception with `raise_message` after commit inspection.

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
- `parent_step_execution_id`: the active parent step Id for correlation, or `None`; it does not transfer workspace objects across processes.
- `source_location`: the source label (`python_module:line`), shared by invocations of the same block. Both step fields are `None` outside active step execution.

Nighthawk does not own workspace filesystem concerns (such as include resolution or host file operations). Those concerns belong to the host application layer that embeds Nighthawk.

Working directory selection for provider backends is configured via `ModelSettings["working_directory"]` (absolute, resolved). When empty (default `""`), backends omit the working-directory option and use the provider default (typically the parent process current working directory).
API:

- `nighthawk.run(step_executor: StepExecutor, *, run_id: str | None = None, usage_meter: UsageMeter | UnsetType = UNSET)`
    - Replaces the current context step executor with the provided step executor.
    - Installs `usage_meter` as the run-level meter when given; omission or `UNSET` creates a fresh `UsageMeter`; `None` is invalid.
    - Resets scoped tools, capabilities, and task references to empty, oversight and lifecycle to `None`, and context construction to `DefaultContextBuilder` for the run.
    - Generates a new `ExecutionReference` for the duration of the `with`.
    - Uses provided `run_id` when given; otherwise generates a new `run_id` (trace root).
    - Always generates a fresh `scope_id`.
    - Can be used even when no step executor is currently set.
- `nighthawk.scope(...) -> Iterator[StepExecutor]`
    - Every configurable parameter defaults to `UNSET`; omission and explicit `UNSET` inherit.
    - Ordinary values replace; empty sequences or mappings clear inherited collections.
    - `Extend(sequence)` appends system/user prompt suffix fragments, tools, or capabilities. Strings and bytes are invalid collection shapes. Repeated capability and fragment entries are preserved.
    - `Merge(mapping)` merges task references. Repeating the identical value is idempotent; a different value for the same name raises `NameConflictError` even if it compares equal. Wrappers capture collection structure without copying entries.
    - `oversight` and `lifecycle` accept `None`, clearing their hooks. Other fields reject it, as well as operation wrappers in unsupported positions.
    - Resolve every field before installing context; failures leave the parent identity and settings intact. Require a run, create a fresh scope Id, and restore the parent after normal or exceptional exit.
    - `context_builder` replaces prompt construction; omission or `UNSET` inherits. See Section 8.2.4.
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

Every executor returns a resolved `StepCandidate` containing an outcome and a shallow read-only `binding_name_to_value` mapping.
The runner requires this contract for custom executors and managed agents alike.
It checks allowed kinds, rejects undeclared output proposals, validates all initialized declared outputs with explicit proposals taking precedence, and independently validates a resolved return value.
Unbound outputs without proposals remain absent; a later Python read raises normally.
Assignment validation and finalization are separate boundaries and may each run validators.
For example, an initialized `Annotated[str, AfterValidator(str.strip)]` output containing `" draft "` finalizes as `"draft"` even without model assignment.
Selected output contracts are fixed at step entry.
A custom executor cannot change finalization contracts by replacing context metadata.

`StepCommit.outcome` uses the frozen Pydantic models exported from `nighthawk.runtime`: `Pass()`, `Return(value)`, `Break()`, `Continue()`, and `Raise(message, error_type=Exception)`.
`Return(None)` is distinct from `Pass()`.
`Raise.error_type` is a resolved exception class, validated before inspection.
An initial Raise discards pending outputs without validating their values, while still rejecting undeclared output names.
Its commit mapping is empty.

`Oversight.inspect_step_commit` receives one validated commit and may return its decision directly or asynchronously.
`Accept` preserves validated values without repeating validators.
`Reject` produces a failed terminal record before generated Python assignments.
`Rewrite` accepts `outcome` and `binding_name_to_value`, each defaulting to `UNSET`; an empty rewrite is invalid.
A supplied mapping completely replaces the committed writes, including when it is empty.
`Rewrite(outcome=Return(None))` explicitly supplies `None`, subject to the return annotation and allowed-kind checks.

Rewrites validate only supplied replacements and do not inspect again.
A bindings-only rewrite retains the resolved return value and never replays an expression.
A final Raise commits no outputs; pairing it with an explicitly supplied nonempty mapping is invalid.
Rewriting away from an initial Raise inherits its empty mapping unless replacements are supplied.
Construct the final exception only after inspection; rejection or a rewrite away from Raise never executes its constructor.

`StepCandidate`, `StepCommit`, and `OperationRequest` preserve live Python values in shallow read-only mappings.
Frozen record structure does not isolate the contained object graph.
Trusted hooks use Rewrite to change a commit; durable history requires explicit copying or serialization.
Finalization cannot undo earlier application calls, object mutation, or validator effects.

### 10.3. Operation inspection

`Oversight.inspect_operation(OperationRequest)` returns `Accept` or `Reject`, directly or asynchronously.
Evaluation, assignment, model return-expression resolution, native tools, and inspection operations share this boundary.
The request includes the execution reference, operation Id, kind, and applicable expression, assignment target, tool arguments, and expected type.
Admission runs before inspection; inspection runs before any operation body or await of its result.
Requests execute serially within each step: in emission order for an ordered model batch, and in admission order otherwise.
Pydantic AI capability hooks cannot reorder an ordered batch across this boundary.
Argument validation is included in that ordering: a rejected or terminally failed call stops subsequent validators as well as tool bodies.
Pydantic AI 2.37 prevalidates batches before execution barriers, so Nighthawk installs context-scoped dispatch compatibility hooks and constrains the supported dependency to the 2.37 minor series.
The hooks preserve Pydantic AI's validators, retries, deferrals, and completion strategies; ordinary Pydantic AI runs outside the Nighthawk dispatch context delegate unchanged.
Under `exhaustive`, upfront Pydantic AI function-call events have `args_valid=None` until the corresponding ordered validation runs; Nighthawk operation records retain the actual admission result.
Explicit Python concurrency inside a helper and separate child steps retain their own execution semantics.

Operation rejection and inspection failures are terminal for that step.
Later queued operations cannot execute their bodies, and an executor cannot suppress the failure and finalize a successful candidate.
Ordinary exceptions from application helpers and native tools propagate to Python.
Runtime syntax, name-resolution, and value-validation errors provide recoverable operation feedback; Pydantic AI control signals retain their native meaning.
Classify failures by origin, not exception class: `NameError` or `ValueError` from a helper body remains an application exception.
An uncaught child Natural exception preserves its child terminal event and also fails its parent operation.
Return expected domain failures as ordinary helper results when the model should act on them; Python helpers may catch specific domain exceptions to construct those results.
Preserve explicit `ModelRetry`, `CallDeferred`, and `ApprovalRequired` signals through Pydantic AI machinery, and distinguish Pydantic validation failures from propagated validator exceptions.
Each completed operation delivers an `OperationFinished` fact with its phase, whether evaluation started, and its live result or original exception.
A notification failure does not change that fact or replay the operation.

Pydantic AI validates native and provided tool arguments before entering the tool body boundary.
A failed schema or argument-validator admission receives an operation Id and an `OperationFinished` record with `phase="admission"` and `evaluation_started=False`, retaining the original exception and unvalidated arguments.
Malformed JSON arguments are retained under `unvalidated_arguments` in the request's argument mapping.
Validation errors remain recoverable through Pydantic AI; ordinary exceptions propagated by application validators terminate the step unchanged.
If a capability repairs invalid arguments, the failed admission remains recorded and the repaired request enters a separately identified inspected operation; the tool body runs once.
Approval and deferral during argument validation remain in Pydantic AI's designated machinery and do not fabricate a failed admission.

Model adapters resolve return expressions through this pipeline once before returning a candidate.
Async Natural returns may await the expression result once; sync Natural returns reject awaitable expression results.
Custom executors may directly construct `Return(value)` without evaluating an expression or creating an operation event.
The runner independently validates the resolved value and all selected outputs before commit inspection.
Expression-syntax policies belong in operation inspection; policies relating return values to committed outputs belong in commit inspection.
A bindings-only rewrite can intentionally diverge from an earlier return value.

### 10.4. Terminal delivery and host ledgers

`nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=callback))` installs a callback accepting `StepFinished` and returning `None` or an awaitable completion.
`on_step_started` runs before preparation, and `on_operation_finished` delivers operation facts.
All three callbacks support direct and asynchronous completion. Omission or `UNSET` inherits; `None` clears. A new run resets lifecycle. Each execution captures the configuration on entry, before preparation. If that configuration has a handler, core attempts notification once while Python can unwind normally; a cleared or absent handler receives no notification. Definition-time parsing, decorator compilation, and failure to enter a required run are outside this boundary. Identity, step context when established, and the step span remain active during delivery and restore afterward, including when delivery fails.

The public `nh.get_lifecycle()` returns the currently scoped configuration object, or `None`, and requires an active run. Hosts can compare it by identity with their installed StepLifecycle at a host-controlled admission boundary. The getter observes configuration; it neither prevents `scope(lifecycle=None)` or nested-run resets nor guarantees later checks use the captured configuration of an already-started step. When a required lifecycle is absent, the host must record any mediation-bypass failure at a boundary that remains active, not rely on the removed callback. Core wraps preparation and finalization failures when no handler translates them; application exceptions recorded by the operation pipeline propagate unchanged.

Generated Python resolves the candidate, performs each selected write assignment, records each completed assignment, delivers the terminal record, and then dispatches return/break/continue. A prepared DSL exception is dispatched after delivery as well. Callback failure prevents further control dispatch but does not undo completed assignments, object mutation, tools, validators, or expression side effects. Partial assignment failures report only writes whose assignments completed. Rejected commits perform no generated assignments.

`StepFinished` is the discriminated union of frozen `StepCompleted`, `StepRaised`, `StepFailed`, and `StepInterrupted`, with respective `kind` values `completed`, `raised`, `failed`, and `interrupted`. All carry `execution_reference`, `processed_natural_program`, `input_binding_name_to_value`, `allowed_step_kinds`, and `assigned_binding_name_to_value`. Preparation-dependent fields are `None` when not established; empty mappings or tuples mean known empty values. Runtime references require both step identity fields. Mapping and tuple structure is snapshotted; contained Python objects retain their exact types and identity. Hosts must serialize or copy deliberately for durable records.

`StepCompleted.outcome` is the final Pass/Return/Break/Continue. `StepRaised` holds a final Raise and its successfully constructed `exception`, including domain exceptions subclassing NighthawkError. Model-selected exception expressions use the operation pipeline; exception constructor failures are `StepFailed` at `raise_construction`, not domain raises. `StepFailed` holds `original_exception`, `failure_stage`, optional `attempted_outcome`, and optional `validated_binding_name_to_value`. Explicit rejection additionally carries `inspection_subject` and `rejection_reason`. Invalid hook decisions remain inspection failures.

`FailureStage` identifies preparation, started_delivery, operation_inspection, operation_execution, operation_delivery, executor, outcome_validation, binding_validation, return_await, return_validation, commit_inspection, rewrite_validation, oversight_rejection, raise_construction, or binding_assignment.
Classification uses structured runtime state, not exception text.
Terminal failures recorded by the operation pipeline propagate unchanged; other internal execution failures use `ExecutionError(step_failed)` chained from the original exception unless delivery supplies a host exception.
DSL Raise propagates its prepared exception.

The callback may append the record to a host-owned authoritative ledger and raise a public host exception containing that exact stored event. For StepFailed, core explicitly chains the callback exception from `original_exception`; for StepRaised, from the prepared domain exception. If the callback rethrows the exact original exception object, core preserves its existing cause rather than chaining the exception to itself. Arbitrary callback exceptions are preserved and never reclassified or redelivered. `StepDeliveryError(execution_reference, cause)` is available for host adapters reporting storage failure; its `delivery_cause` does not imply a durable event exists. During ordinary execution failure, the outward chain prioritizes the execution exception and the storage cause remains available in `delivery_cause`.

Executor and commit-inspector exceptions produce StepFailed delivery even when the original exception is already a host-defined NaturalExecutionError. Core does not recognize host exception types or deduplicate host records. A handler that finds an already-recorded host exception must verify both matching execution identity and association with the exact stored event before skipping another append, then rethrow that exception to preserve its type. Returning normally instead causes core to raise ExecutionError chained from the host exception. A nested execution has a different step_execution_id and needs its own terminal event even when its failure carries a recorded child exception. Hosts should not suppress every exception of their public type indiscriminately.

On StepCompleted delivery failure, completed assignments remain visible and the callback's exception propagates. There is no original execution failure to fabricate as its cause, and the already-classified StepCompleted is not rewritten into StepFailed. Hosts may classify the overall operation as a commit/delivery failure and record their own anomaly; that classification is separate from core's execution result and does not authorize retrying the execution.

`StepInterrupted` holds the active `failure_stage` and original cancellation or process-control `BaseException`.
An asynchronous notification receives a shielded delivery attempt bounded by `StepLifecycle.interruption_delivery_timeout`; a timed-out notification is cancelled.
The synchronous bridge shares that deadline with its private event-loop shutdown, including operation notifications, so shutdown does not wait again for a cancellation-resistant handler.
Pending cleanup may remain incomplete when that private loop closes; an asynchronous caller owns the later shutdown of its own event loop.
If delivery also fails, the original interruption still propagates, explicitly chained from the delivery exception.
Cancellation, SystemExit, KeyboardInterrupt, and GeneratorExit are not translated into ordinary host execution failures.

The guarantee is one in-process delivery attempt, not a transaction across Python and storage. A callback that appends and then raises cannot be distinguished automatically from an uncertain append failure. Hosts deduplicate and retry storage by `step_execution_id`, never by replaying a Natural execution. Hard process termination and unavailable storage cannot guarantee durable delivery. OpenTelemetry is optional diagnostics, not the authoritative ledger. Terminal tracing follows the typed classification: await failures receive `nighthawk.step.failed` and exception/error status; interruptions receive `nighthawk.step.interrupted`; delivery failures may add `nighthawk.step.delivery_failed` without a second execution terminal event.

Host integrations can separate responsibilities: the executor boundary admits and accounts for execution, `inspect_step_commit` approves or rewrites the resolved candidate before assignment, and StepLifecycle records the terminal result and translates failures. Preparation can fail before executor admission; terminal consumers must accept records without a matching admission event. The shared `execution_reference.step_execution_id` correlates executor StepContext, inspectors, and terminal records without storing a live StepContext or pending-commit ContextVar. Terminal records make type-erasing validation workarounds unnecessary for catching binding, expression, and return-validation failures, but do not remove the host's other validation requirements. No external host code or host-specific serialization schema is provided by core. The deterministic ledger fixture in `tests/governance/test_step_ledger.py` demonstrates recording even when Python inside the Natural function catches the translated host exception.

#### 10.4.1. Delivery failure cases

| Delivery failure | Execution fact and boundary behavior |
| --- | --- |
| `on_step_started` fails | No preparation or executor body starts. Attempt terminal delivery with `StepFailed` identifying started delivery as the failure stage. |
| An operation succeeds, then `on_operation_finished` fails | Preserve the successful `OperationFinished` fact and its effects. Stop queued operations and finalization; attempt `StepFailed` terminal delivery identifying operation delivery as the failure stage. |
| An operation fails, then its notification also fails | Retain both the operation failure and notification failure. Stop the step and attempt terminal delivery; do not convert the notification error into recoverable model feedback. |
| Caller writes succeed, then `on_step_finished(StepCompleted)` fails | Keep the writes and completed record. Propagate delivery failure without emitting `StepFailed` or executing again. |
| Terminal delivery of `StepRaised` or `StepFailed` fails | Preserve the original execution record and cause alongside delivery failure; do not emit another terminal event. |

The terminal attempt precedes any Python `except` outside the Natural boundary.
`StepLifecycle.interruption_delivery_timeout` defaults to one second for bounded asynchronous interruption cleanup; it does not bound a blocking synchronous hook or guarantee storage.
Cancellation while approval is pending does not start the operation.
Cancellation after execution starts leaves possible external effects for host reconciliation.
Host domain operations should validate, approve, execute, and record their own concrete requests whether invoked by Python or Natural; core hooks add intervention points without replacing that authority.

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

- This interpolation mechanism is distinct from the `nh_eval` tool. f-string evaluation runs in the normal Python execution context, while `nh_eval` evaluates expressions inside the Natural execution environment (`StepEnvironment`).

Decision:

- Any Python expression is permitted inside f-string `{...}` segments under the trusted-input model.
- There is no implicit placeholder replacement or template preprocessing step for Natural blocks.

## 12. Persistence and user-managed state

Nighthawk does not define a built-in persistence or memory model.

If you want a long-lived object, define it yourself and expose it as an ordinary Python value through lexical state or scoped task references. Runtime expressions can read it and `nh_eval` can mutate it in place. Inherited working associations support child execution but do not provide durable persistence.

### 12.1. Carry pattern

The carry pattern is an idiomatic use of read bindings for cross-block context continuity. Pass a mutable Python object (e.g., `list[str]`) as a read binding (`<carry>`) and instruct the LLM to mutate it in place via `nh_eval`. Rebinding an existing Python name requires an output declaration, so a read-only declaration preserves the caller's name association while allowing object mutations.

For practical examples and design tips, see [Patterns](patterns.md#cross-block-composition).

## 13. Error handling

Nighthawk defines public library errors rooted at `NighthawkError`, plus an internal `ToolBoundaryError` signal for recoverable tool failures.

Exception hierarchy:

- `NighthawkError`: Base class for public Nighthawk library errors.
    - Raised when: runtime preconditions fail (e.g. no active run context, missing step executor).
- `NaturalParseError(NighthawkError)`: Natural block parsing or frontmatter parsing failed.
    - Raised when: the sentinel is missing, bindings are invalid, frontmatter YAML is malformed, or AST extraction fails.
- `ExecutionError(NighthawkError)`: runtime internal failure; `step_failed` holds its `StepFailed` record and `__cause__` holds the original exception.
    - Raised when: the LLM returns invalid JSON, an outcome kind is disallowed, or boundary value validation fails outside the operation pipeline.
    - A DSL Raise defaults to ordinary `Exception`; application operation failures retain their own exception types.
- `ToolEvaluationError(NighthawkError)`: Exported exception type; current common expression operations use `OperationError` for recoverable runtime failures and propagate application exceptions.
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
    - `run_step(*, request: StepRequest) -> StepCandidate`
- `AsyncStepExecutor`
    - `run_step_async(*, request: StepRequest) -> StepCandidate`

The type alias `StepExecutor = SyncStepExecutor | AsyncStepExecutor` is the union accepted by `nighthawk.run()`.

`StepRequest` holds `execution_reference`, `processed_natural_program`, `step_context`, ordered `output_binding_names`, `binding_name_to_type`, `return_annotation`, `allowed_step_kinds`, and `allow_awaitable_return`.
Its context owns the environment; no second namespace is assembled for the executor.
Both methods return `StepCandidate(outcome=..., binding_name_to_value=...)` containing live values.
Finalization remains mandatory, as specified in Section 10.2.

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
from nighthawk.runtime import StepCandidate, StepRequest
from nighthawk.runtime.step_executor import AsyncStepExecutor


class MyExecutor(AsyncStepExecutor):
    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        # Read the program and selected contracts from request.
        # Use request.step_context.environment for live name resolution.
        # Runtime eval_expression_async and assign_tool_async use the common
        # operation pipeline, including inspection and lifecycle delivery.
        # Return resolved live values; the runner validates the final candidate.
        ...
```

`SyncStepExecutor` follows the same shape with `run_step` instead of `run_step_async`.

For most custom backends, wrapping a Pydantic AI `Agent` via `AgentStepExecutor.from_agent` (see [Executors](executors.md#custom-backends)) is simpler than implementing the protocol directly. Direct implementation is appropriate when the backend does not use a Pydantic AI agent at all.

See the [API Reference](api.md#base) for the full protocol definition.

### 14.4. Nested execution acceptance example

This is a proof example for nested Natural execution and materialized parent state. Its fixed counter and labels isolate the semantics; they are not intended as a useful application by themselves. `block_two` receives its iteration context from the active parent step, not a Python parameter.

```python
import nighthawk as nh


@nh.natural_function
async def block_two() -> str:
    message = ""
    """natural
    Inspect <i>, the parent's current one-based iteration.
    Set <:message> to 'fifth' on iteration 5, 'eighth' on iteration 8,
    and 'ordinary' on every other iteration.
    """
    return message


@nh.natural_function
async def block_one() -> list[str]:
    messages: list[str] = []
    """natural
    Call <block_two> ten times in sequence.
    Before each call, use nh_assign to set the temporary name i to the
    current one-based iteration, from 1 through 10.
    Collect the returned strings in <:messages> in call order.
    """
    return messages
```

The executor materializes `i` before invoking the child; mentioning a counter in prose does not create runtime state. The fifth and eighth calls inherit 5 and 8. Later parent assignment does not change an already started child's inherited `i`.

Call `await block_one()` inside `nh.run(executor)`.
The expected successful result is four `ordinary` entries, `fifth`, two `ordinary` entries, `eighth`, and two `ordinary` entries.
Deterministic executor tests establish the environment mechanics; model evaluations measure whether an LLM follows the ten-call instruction.
The runtime must not claim that type validation of `list[str]` proves the count or contents.

Custom executors materialize parent working names using `assign_tool_async` and call children using `eval_expression_async`, both exported from `nighthawk.runtime`.
Their synchronous counterparts are `assign_tool` and `eval_expression`.
These entry points include admission, inspection, ordering, workspace updates, and lifecycle delivery; do not mutate environment views or build a second operation policy.
Directly constructing a `StepCandidate` proposes final values but creates no intermediate operation or inherited temporary state.

### 14.5. Behavioral acceptance cases

The contracts above own these semantics; this table collects review cases for implementations and adapters.

| Scenario | Required result |
| --- | --- |
| Child Python parameter `i=99`, parent `i=5`, injected `i=8` | Bare `i` is 99; explicit parent access is 5; explicit task access is 8. |
| Child module global `i=99`, parent `i=5` | Bare `i` is 99; inherited state does not override module globals. |
| Child Python local `i` is unbound, parent `i=5` | `UnboundLocalError`; no fallback. |
| Child local `i=None`, parent `i=5` | Resolve `None`. |
| No lexical `i`, parent `i=5`, injected `i=8` | Resolve 5; if no parent has it, resolve 8. |
| Inner scope clears task references | Cleared injection cannot reappear through copied parent globals or task values. An actual parent temporary with that name remains parent state. |
| Visible local `value=7` evaluated as `(lambda: value)()` | Return 7 using the same environment as direct lookup. |
| Visible local `value=7` evaluated as `list(value + i for i in range(2))` | Return `[7, 8]`; the generator is consumed within one operation. |
| Existing closure cell is rebound before the Natural call, or an explicit global/nonlocal output is committed | Observe the latest cell value, or update the declared module/cell destination; do not use a captured value copy. |
| `nh_eval` receives `(value := 8)` | Reject the named assignment and direct the model to tracked assignment. |
| `nh_assign` targets an existing Python name not declared as an output, and its right-hand side calls a helper | Reject before invoking the helper; a new eligible temporary name is allowed. |
| An initialized declared output is never assigned by the executor | Include it in non-Raise finalization; its validators may transform it or reject it. |
| Local `a: int = 0` is declared as `<:a>` and receives `'invalid'` through `nh_assign` | Reject at assignment validation and retain its previous working value; final output validation remains a separate boundary. |
| A Pydantic model with `validate_assignment=True` has `confidence: float = Field(ge=0.0, le=1.0)` and receives `2.0` | Reject through the model-aware assignment path; the plain `float` annotation alone is insufficient. |
| A dotted target is an annotated ordinary object attribute or a model field with assignment validation disabled | Report an unsupported target and recommend a Python helper. Report any earlier right-hand-side effects. |
| An eligible assignment's helper mutates a list, then its return fails validation | The pending name assignment fails; the list mutation remains. |
| An admitted dotted assignment has an intermediate getter that fails | The right-hand side has already run once; its effects remain, and no final setter runs. |
| One model batch assigns the parent temporary `i=5`, then invokes a child with no lexical `i` | Run in that order; the child inherits 5. Awaited approval does not allow the child call to overtake the assignment. |
| Child assigns a temporary inherited name | Parent name is unchanged; child lookup uses its own working value. |
| Child mutates an inherited list | Parent observes the same list mutation. |
| Host rejects the final candidate after an earlier approved operation | No pending caller assignments; the earlier operation remains recorded and effective. |
| Host awaits approval and rejects | Operation body is not invoked; rejection has a correlated record. |
| Host rewrites the output mapping to `{}` | No caller name assignments; workspace values are not automatically added back. |
| Host supplies `Rewrite(outcome=Return(None))` | Validate `None` against the return contract; retain the candidate's output mapping. |
| Initial `Raise` has invalid pending output values | Skip final validation of discarded values and inspect an empty output mapping. |
| Host rewrites `Raise` to `Pass` without replacement outputs | Commit no outputs; discarded values remain absent from the candidate. |
| Model evaluates `inspect(some.function)` | Evaluate the argument normally, describe the resulting value, and deliver one evaluation-operation record. |
| A supported explicit output annotation cannot be resolved | Fail preparation; do not infer a weaker contract from its initial value. |
| `inspect` cannot resolve a presentation-only annotation | Preserve its available spelling and report the limitation; leave the output contract unchanged. |
| An empty list has a captured `list[Finding]` output annotation | Name-based discovery exposes that contract; value inspection does not infer it from the empty list. |
| Runtime lookup cannot find a name in a generated expression | Return a recoverable lookup observation. A `NameError` raised inside a called helper instead propagates. |
| A child Natural raises during a parent operation | Preserve the child terminal event and propagate through ordinary Python exception handling; an uncaught exception also fails the parent. |
| Caller assignments succeed and terminal storage fails | Assigned values remain; delivery failure propagates without rerunning execution. |
| Caller catches a Natural failure in Python | The terminal delivery attempt already occurred. |
| Model returns an expression with effects | The common engine evaluates it once; validation, approval, and rewrite do not replay it. |
