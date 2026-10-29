# Nighthawk specification

This document is the specification for Nighthawk.

## 0. Document scope and alignment policy

This document defines Natural syntax, runtime behavior, and host-facing contracts.
[API reference](api.md) owns signatures and the public symbol inventory; task-oriented guides explain usage.
The [philosophy](philosophy.md#core-responsibility) defines the Core boundary and principles for changing it.

## 1. Goals

Execute natural-language descriptions of work inside ordinary Python programs, from small classifications to autonomous agent tasks.
Python owns deterministic procedure, execution points, permitted control results, and declared names receiving validated writes.
Natural supplies judgment using accessible Python lexical values and explicitly scoped task references.
In `execute`, read bindings express dependencies and presentation priority, not an access allowlist.
In `decide`, only explicitly referenced input values are sent as evidence.
Essential inputs should normally be explicit parameters; scoped task references declare shared contextual dependencies.
Calling a Natural function does not copy its caller's working values into the child's environment.

Ordinary applications need `function`, output contracts, and an executor selected through `run`; `scope` varies configuration and task injection.
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
Each block starts a fresh model interaction; neither a parent's working values nor its model message transcript are implicitly copied into a child.

## 3. Hard constraints

- Python 3.13+.
- Models are selected explicitly; provider installation and identifier examples are in [Pydantic AI providers](pydantic-ai-providers.md).
- Coding agent backends are installed via extras: `claude-code-sdk`, `claude-code-cli`, `codex`. Pydantic AI provider dependencies are installed separately (see [Pydantic AI providers](pydantic-ai-providers.md)).
- Threat model: Natural source, imported Markdown, prompt fragments, and declarations are trusted program inputs authorized by the host for their intended purpose, permissions, and environment.

This trust requirement applies whether a program is repository-managed or generated at runtime.
The host owns authorization policy, which may be established in advance and applied automatically.
Core accepts trusted program inputs; it does not verify their approval, prove their correctness, or isolate their execution.
Authorship and repository storage alone do not establish authorization.
Hosts must authorize the program and its execution path before program preparation or execution can produce effects.
Generation, revision, and reuse do not implicitly grant additional authority; changed execution conditions must be covered by the host's policy.
Untrusted task data belongs in binding values, not source interpolation or template preprocessing; this separation does not guarantee that a model will ignore instructions in data.

Authorization does not change supported source forms.
`@nh.function` requires [available Python function source](#812-supported-python-source-forms); [embedded steps](#explicit-embedded-steps) accept host-selected Natural text and declarations without Python function source inspection.

## 4. Terminology

- Natural block: a Python docstring or inline string literal beginning with an `execute` or `decide <:output>` header.
- Natural DSL: the constrained syntax inside a Natural block (token binding plus free-form instructions).
- Python locals (`python_locals`): the actual Python local variables in the function's frame.
- Python globals (`python_globals`): the Python module globals for the compiled function.
- Step locals (`step_locals`): captured working values and tracked assignment bookkeeping; the environment owns full expression lookup.
- Step environment (`StepEnvironment`): provenance-preserving Python, task, and step views with one effective lookup contract.
- Step globals (`step_globals`): internal namespace storage; it is not an independent lookup authority.
- StepContext: a mutable, per-step object (one Natural block execution) passed to tools and executors.
    - Required fields include `execution_reference`, containing invocation identity and source location.
    - Model selection and prompt policy are owned by `StepExecutorConfiguration`; StepContext does not carry model configuration.
- Locals summary: a bounded presentation of selected working values and their available contracts, included in the LLM prompt.
- Prompt suffix fragment: additional prompt text appended to the end of the effective system prompt or user prompt for the duration of a scoped override.
- Step: one invocation of a Natural block, with its own execution identity.
- Block kind (`BlockKind`): `execute` for action-oriented instructions or `decide` for a typed judgment.
- Model outcome (`ModelOutcome`): a model's proposed control flow for `execute`, which may contain unevaluated expressions.
- Step outcome (`StepOutcome`): resolved Python control flow, represented by `Pass`, `Return`, `Break`, `Continue`, or `Raise`; values and exception classes are live Python objects.
- Step candidate (`StepCandidate`): a step outcome plus the proposed output selection, pending runtime validation and commit inspection.
- Outcome kind: the required `kind` field on an outcome object. The baseline kinds are `pass`, `return`, `break`, `continue`, and `raise`.
- Allowed outcome set: the set of outcome types allowed for a specific Natural block instance, derived from syntactic context and deny-only frontmatter.
- Frontmatter: optional YAML metadata at the start of a Natural program, delimited by `---` lines.

## 5. User-facing API

### 5.1. Decorator

- `nighthawk.function`
    - Decorator that compiles a function containing Natural blocks into an LLM-backed implementation.
    - Compilation happens at decoration time; blocks execute when the synchronous body runs or the asynchronous coroutine executes.
    - Bare `@nh.function` and empty `@nh.function()` forms are supported. Models belong to `run` and `scope`; the decorator accepts no model configuration.
    - Decoration does not initialize providers. Optional whole-function admission precedes body effects; see [embedded execution boundaries](#embedded-execution-boundaries).
    - Note: The decorator requires the function source to be available for inspection.
    - `inspect.unwrap(function)` follows the original function; signatures and original source remain conventional. `nh.get_transformed_function(function)` exposes the compiled body sharing the original module globals. It follows outer wrappers and supported method descriptors, requires no run, and raises `TypeError` for invalid inputs or wrapper cycles. Execute through the decorated function to establish runtime context.
    - Instrumentation preserves original defaults without evaluating them again, live closure cells, and defining module globals. Generated helper names avoid application bindings, including names appearing inside Natural text.
    - `nh.get_function_definition(function)` supplies exact transformation correspondence without following unrelated outer wrappers; see [whole-function interception and exact definitions](#whole-function-interception-and-exact-definitions).

### 5.2. Configuration

- `StepExecutorConfiguration`
    - `execution_model`: Provider-qualified string or an existing Pydantic AI `Model`, used by `execute`. Default: `None`.
        - Examples: `openai-responses:gpt-6-luna`, `openai-responses:gpt-6.1-sol`.
        - Special cases:
            - `claude-code-sdk:default`, `claude-code-cli:default`, and `codex:default` select the backend/provider default model (no explicit model selection is sent to the backend).
    - `decision_model`: Provider-qualified string or an existing Pydantic AI decision model, used by `decide`. Default: `None`. A missing model fails before a request for that block kind.
    - `decision_model_settings`: Decision model settings, defaulting to an empty dictionary; accepts the same forms as `execution_model_settings`.
    - `execution_model_settings`: execution model/backend overrides, defaulting to an empty dictionary. Accepts a `dict[str, Any]` or a backend-specific `BaseModel` instance (auto-converted to dict); rejects `None` and `UNSET`. An empty dictionary adds no overrides and preserves model-owned defaults. Forwarded to Pydantic AI Agent calls. Each coding agent backend defines a settings class (`CodexModelSettings`, `ClaudeCodeSdkModelSettings`, `ClaudeCodeCliModelSettings`) -- see [Coding agent backends](coding-agent-backends.md) for field-level documentation.
    - `tokenizer_encoding`: tokenizer encoding identifier for approximate token budgeting. `None` means auto-resolve by model name, then fallback to `o200k_base`.
    - `prompts`: prompt templates used for execution.
        - `system_prompt_template`: system prompt template that defines the step execution protocol.
        - `user_prompt_template`: full user prompt template including section delimiters.
    - `context_limits`: limits for rendering dynamic context into the prompt.
    - `json_renderer_style`: [headson](https://github.com/kantord/headson) rendering style used in prompt context and projected tool-result previews. Default: `"default"`. Available values: `"strict"` (valid JSON, no annotations), `"default"` (pseudo-JSON with omission markers like `…`), `"detailed"` (JS-like with inline comments such as `// N more`).
    - `system_prompt_suffix_fragments`: optional baseline system prompt suffix fragments for this executor configuration.
    - `user_prompt_suffix_fragments`: optional baseline user prompt suffix fragments for this executor configuration.

Runtime Models are borrowed resources. Configuration validation, shallow derivation, managed Agent construction, and scope replacement preserve the exact supplied Model. Two instances with the same name can have different authentication. Nighthawk does not clone, reconstruct, mutate, enter, or close supplied clients. The host owns client lifetime and any SDK restrictions on threads or event loops.

For `execute`, a managed executor uses one common prompt, tools, dynamic output contract, capability, settings, and oversight path for either string or borrowed model selection.
The decision path uses typed questions and explicit evidence instead of execution tools and prompt previews. Configuration model settings override Model defaults under Pydantic AI's existing precedence, with per-run capabilities retaining their ordinary behavior.
Explicit tokenizer encoding wins; otherwise inference uses the string's model component or `Model.model_name`, falling back to `o200k_base` only for unknown model names.
Invalid explicit encodings and encoding data loading failures propagate; a loading failure never selects a different tokenizer.

Model instances cannot be reconstructed from mappings. Configuration `repr` and `str` omit the model without invoking its representation. Python and JSON serialization including a live model field raises `PydanticSerializationError` with no traversal of the Model; explicit exclusion or inclusion of only other fields succeeds, including nested Pydantic records. String configurations remain serializable. A partial dump cannot reconstruct authenticated execution resources. `model_copy(update=...)` preserves Model identity by default but does not validate updates; callers must supply valid typed settings.

Hosts supply authenticated Providers and Models in memory. Nighthawk does not extract credentials or transport them through environment variables, operating-system process launch arguments, prompts, or its diagnostic payloads. Pydantic AI environment reads and existing provider-string authentication are supported. Docker credential delivery, arbitrary Model behavior, and external logging are host concerns. Forwarding settings such as `openai_store=False` proves request forwarding, not remote retention compliance.

Agents supplied through `from_agent` own model selection for `execute`.
That entry point rejects a Model instance in `configuration.execution_model`; an optional string describes the borrowed execution model and supports tokenizer inference without configuring the agent.
Changing that execution model selection raises `NighthawkError` before scope installation; keeping it permits rendering changes.
For both managed and borrowed execution agents, `decision_model` independently selects the model for `decide` and accepts a string, a borrowed Model, or `None`.
For managed execution agents, full configuration replacement selects its supplied execution Model.
Scopes restore the exact parent executor on exit or error, and the host retains ownership of borrowed clients and agents.

### 5.3. Supporting types

- `StepPromptTemplates`
    - Prompt templates used for step execution.
    - `system_prompt_template`: system prompt template.
    - `user_prompt_template`: user prompt template.
- `StepContextLimits`
    - Limits for rendering dynamic context into the LLM prompt.
    - Fields: `locals_max_tokens`, `locals_max_items`, `globals_max_tokens`, `globals_max_items`, `value_max_tokens`, `object_max_methods`, `object_max_fields`, `object_field_value_max_tokens`, `tool_result_max_tokens`.
- `JsonableValue`
    - Type alias for JSON-serializable Python values (`dict | list | str | int | float | bool | None`).
- `ExecutionReference`
    - Frozen dataclass representing runtime execution identity.
    - `run_id`: the Id of the active run; nested runs create independent identities unless an Id is supplied explicitly.
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

- `execute\n` or `decide <:name>\n`

The Natural program is derived from the docstring by:

1) Removing the block header (`execute` or `decide <:output>`) and its newline.
2) Normalizing indentation by applying `textwrap.dedent` to the remainder.

This normalization exists because Natural blocks are typically indented inside Python code. It ensures the Natural program text is stable regardless of surrounding Python indentation.

Recommended form:

- `"""execute\n..."""`

2) Inline Natural blocks

Inside a function body, a standalone expression statement whose AST is syntactically a string literal begins with:

- `execute\n` or `decide <:name>\n`

The expression value must be either:

- A plain string literal, or
- An f-string literal

The Natural program is derived at runtime by removing its block header and applying `textwrap.dedent` to the body.

Docstring note:

- A docstring Natural block is always a plain string literal.
- Even if an f-string is the first statement in a function body, it is not a docstring, and it is treated as an inline Natural block.

Decision (inline shape):

- The inline Natural block is defined by the AST shape "expression statement containing a string literal (including f-strings)".
- Parentheses do not matter.

Sentinel rules (both docstring and inline):

- Headers are case-sensitive, with no leading blank lines or whitespace.
- Execution blocks begin with exactly `execute\n`.
- Decision blocks begin with `decide <:name>\n`, with exactly one output binding and no trailing whitespace.
- An f-string header must be static; its body may interpolate trusted source values.

### Typed decision blocks

A `decide <:name>` header declares exactly one destination; a Pydantic output model can contain multiple questions.
The body must contain a nonempty judgment instruction and no output binding markers or control-flow frontmatter.
It supplies Pydantic AI `Agent.instructions`; the header never becomes a model instruction.
Explicit input bindings provide complete serialized evidence, with supported top-level multimodal content preserved.
No execution context previews, implicit locals/globals, execution prompt templates, execution suffix fragments, context builders, or scoped action tools are sent.
Unsupported evidence serialization fails rather than silently summarizing a value.
Pydantic AI's decision model owns supported output schemas and the mapping to questions; Nighthawk does not implement another planner.
Examples include booleans, finite choices, bounded probabilities, described rubric levels, and structured models; unsupported schemas fail before an API request.

A decision produces only `Pass` and an explicit mapping containing its one destination.
An omitted answer, refusal, invalid result, or unsupported configuration fails; existing destination values never substitute for a missing answer.
Custom executors must obey the same output requirement.
Normal runtime validation and commit oversight run before Python assignment; successful completion requires the destination to remain present after oversight.
Python owns subsequent branching, retries, and actions.
Scoped Pydantic AI capabilities can observe the request, but decision requests cannot expose action tools or select an execution model.

Terminal lifecycle records include `block_kind` and, when a managed model run returned, its native `AgentRunResult` in `model_result`.
Use `record.execution_reference.step_execution_id` to correlate each invocation, including repeated decisions in one function.
`record.model_result.response.provider_details` preserves confidence, probabilities, and unrounded scores without replacing typed Python results.
`record.model_result.usage` exposes that run's usage; the common meter includes completed execution and decision model runs.
Model evidence remains available if later commit inspection or writeback fails.
When the model run itself fails before returning, `model_result` is absent; Core does not invent provider details or usage.

## 7. Natural DSL: bindings

`execute` declares output bindings in its body, including multiple destinations and repeated references to a destination.
`decide` declares its single output in its header; its body contains read bindings and judgment criteria.
A marker declares an output, not an assignment event; instruction and tool execution determine intermediate assignments in `execute`.

The Natural program may contain bindings with angle brackets:

- `<name>`: read binding. Resolve the current value of `name` in the effective environment and prioritize its presentation to the LLM.
- `<:name>`: output binding. Authorize a pending Python name assignment at the block boundary.

Resolution note:

- Read bindings use the same environment as runtime operations: step assignments, Python lexical names, then scoped task references.
- An unbound Python local or closure cell blocks fallback to a same-name task value.
- If a read binding is missing or unbound, preparation fails with the original `NameError` or `UnboundLocalError` stored in `StepFailed.original_exception`. Unless terminal delivery supplies a host exception, the caller receives `ExecutionError` chained from that original exception (see [Section 10.4](#104-terminal-delivery-and-host-ledgers)).

Constraints:

- `name` is a simple identifier (no dotted paths).
- Prefix a marker with a backslash to make it literal: `\<name>` and `\<:name>` declare neither reads nor writes.
  The backslash is removed when either block kind presents the instructions to its model.
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

`StepContext.environment` owns effective lookup and separate read-only views of Python, task, and step bindings.
Lookup prefers tracked step assignments, then Python locals/cells/globals/builtins, then scoped task references.
Python locals are captured from the instrumented function at block entry; closure cells and defining module globals remain live.
Unbound locals and cells retain Python's blocking semantics.
Unrelated caller frames are not lexical dependencies.

A child establishes its own Python lexical environment and receives the active scope's explicit task references.
Parent arguments, locals, cells, and step temporaries are not additional name sources, including through intermediate helpers and further descendants.
A value passed as an argument, accessible through the child's own Python globals or cells, or deliberately supplied through `task_references` remains available from that source.
No name or object-identity blacklist is applied.
This controls implicit name availability, not Python introspection, object access, or security isolation.

`step_locals` holds captured Python working values and tracked assignment bookkeeping; it is not the complete effective environment.
Use `environment.resolve`, its source views, or the common runtime operations for name lookup.
Generated assignments write validated, selected `<:name>` values back to the corresponding Python locals, cells, or globals after finalization.
Automatic output selection considers all initialized declared outputs, even without tracked assignment or mutation marks.
An explicit candidate mapping selects exactly its entries without supplementing other initialized outputs.
Every block starts a fresh model interaction; Python values and scoped task references carry deliberately shared context between blocks.

#### 8.1.1. Environment views and lookup

All environment views have string keys and shallow read-only structure.
Optional constructor mappings default to empty and reject `None`; explicitly supplied mappings are copied without testing their truthiness.

| Member | Meaning |
| --- | --- |
| `python_name_to_value` | Values accessible through the block's Python lexical environment, including enclosing cells, globals, and builtins. |
| `task_name_to_value` | The resolved mapping injected by the active scope. |
| `step_name_to_value` | Values explicitly assigned in this step, including pending declared outputs and temporary working names. |
| `resolve(name, *, source="effective")` | Resolve a name from the effective environment or specifically from `python`, `task`, or `step`. |
| `describe(name)` | Describe the effective origin, shadowed alternatives, write eligibility, captured annotation, selected output contract, and usable references without evaluating the value's properties. |
| `snapshot_working_state()` | Explicitly copy bound Python working locals and cells plus tracked assignments into a new dictionary; exclude module globals, builtins, task references, and runtime names. |

Effective lookup uses tracked step assignments, Python locals, enclosing cells, globals, builtins, then task references.
A Python binding with value `None` is present; an unbound local or cell blocks fallback with its Python lookup error.
Absence everywhere raises `NameError`.
Step temporaries may shadow task names, but an existing Python name can be rebound only as a declared output.
Select a shadowed value with `environment.resolve(name, source="task")` or `source="python"`; selection applies to that value reference, not an entire expression's namespace.
Bindings, evaluation, assignment expressions, model-selected return and exception expressions, and discovery share this resolution contract.
There is no parent environment view or supported `parent` source.
Ordinary Python helpers can select shared values with `resolve` and return a dictionary for `scope(task_references=...)`.
An explicit working-state snapshot retains object identity and does not by itself publish anything to another step.

#### 8.1.2. Supported Python source forms

The baseline is source-available ordinary `def` and `async def` functions on supported CPython 3.13+ versions.
Functions generated at runtime and decorated with `@nh.function` must satisfy the same source-availability and lexical requirements.
Ordinary Python type checkers and refactoring tools do not resolve references or infer assignments inside Natural text.
Nighthawk resolves those references and validates selected output values at runtime; Natural bindings do not inform static analysis of the surrounding Python code.

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
Recursive calls retain ordinary Python name lookup, including later module-global rebinding and enclosing closure cells.
Calling the public wrapper recursively enters the same function-admission boundary as any other call.

#### 8.1.3. Shared values and expression lifetime

Task references are shallow-copied when a scope is entered and captured in each step's environment during preparation.
Omission or `UNSET` preserves the active scope's references; a mapping replaces them, `{}` clears them, and `Merge` adds references with identity-based conflict checking.
Python helpers run where the application calls them, not as implicit preparation callbacks.
Later replacement of a scope or rebinding of a parent's working name does not change an already prepared environment; mutations of explicitly shared objects remain visible.
Child temporary rebinding does not write back to its parent, and siblings have separate workspaces.
Cleared task references cannot reappear from materialized parent temporaries or older ancestors.
Nested scopes restore their enclosing references on normal exit, failure, and cancellation; independent concurrent contexts can select different references.
An ordinary coroutine uses its execution context, and a Task uses its selected creation context.

Helpers may join children synchronously or asynchronously, including concurrent children.
Execution parentage, scoped controls, usage accounting, inspection, and lifecycle remain independent of which values are shared.
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
It identifies a collision-free inspection reference for details missing from the snapshots.
Inspection returns a metadata dictionary rather than the original value.
When native media is included, the preamble directs perceptual judgments to that attached content.
Environment discovery and named-description expressions appear beside omitted context entries instead of in the preamble.
The preamble does not repeat runtime-root protection or output-validation rules; their runtime enforcement is unchanged.

#### 8.2.2. Locals summary

The locals summary presents current Python working locals and step assignments.
Entries use ordinary Python names and a single type annotation, such as `result: int = 0`.
The selected output contract takes precedence over an available declaration annotation; otherwise the current value's type is used.
Neither `[output: ...]` / `[annotation: ...]` prefixes nor `<:name>` decorations appear in the context sections.
Natural source declarations and runtime assignment and validation rules remain authoritative.
Explicit dotted entries do not inherit their root's annotation or output contract.
Provenance and shadowed alternatives are available through environment discovery and named descriptions.
The default prompt presents effective bindings without a collision scan or source-selection instructions.
These descriptions do not change the live environment or its validation contracts.

Selection:

- Python working locals, step assignments, and declared outputs are eligible, including application names starting with `__`.
  Other available names remain retrievable through environment discovery.
- Instrumentation allocates internal names that avoid application bindings and names present in the function source, including Natural text.
  Only the internal bindings actually used by that transformed function are excluded from its Python environment, initial context, and explicit working-state snapshots.
  Application names are not excluded by a shared prefix; their lexical priority, unbound status, and explicit source references remain intact.
  The separate prohibition on `__` segments in assignment-tool targets still applies (Section 8.3).

Ordering:

- Explicit references and outputs precede other Python working locals.
  Ordering within a priority class is lexicographic.

Rendering format:

- Unbound names have an annotation and `# unbound`, without an invented initial value.
- `TypeAliasType` values (PEP 695) retain their name in a comment and explicitly omit expansion that would require evaluation.
- Callable values use static, best-effort signature metadata shared with value inspection.
  The binding annotation is followed by a signature comment, such as `helper: Callable  # (base: int, bonus: int) -> int`.
  A selected contract or declaration annotation replaces the generic `Callable` annotation when available.
  Unresolved annotation spelling is preserved without evaluation.
    - If the callable has a meaningful docstring, the first line is appended to the comment.
    - If the callable is async, `async` precedes the signature, as in `helper: Callable  # async (operation: str) -> str; first_line`.
      Known async status is also retained when the signature is unavailable.
    - Each callable retains its distinct Python reference, including the object path for methods.
    - If signature acquisition requires executing an application descriptor, the comment states `signature unavailable` without reading that descriptor.
- Object capability values (non-callable, non-scalar, non-container values):
    - Header line: `name: TypeName  # instance`.
      If a selected contract or declaration differs from the runtime type, the annotation shows that contract or declaration and the comment identifies the actual instance type.
    - Public method lines use callable rules above with the full `name.method` reference.
    - Public field lines use the value preview rules below.
    - Public means names that do not start with `_`; private and dunder names are excluded.
    - Properties are not evaluated.
    - Field discovery uses safe sources: instance `__dict__`, dataclass fields, Pydantic model fields, and readable public `__slots__` entries.
    - Class attributes that are public and non-callable are included as fields.
    - Method expansion is bounded by `context_limits.object_max_methods`; field expansion is bounded by `context_limits.object_max_fields`; each field value preview is bounded by `context_limits.object_field_value_max_tokens`.
    - If object member limits are exceeded, comments identify the object and the number of omitted methods or fields.
- Complete previews of ordinary scalar, list, and string-keyed dictionary values use Python literals, including `None`, `True`, and `False`.
  JSON preview rendering and its configured style still supply bounded summaries; JSON literals outside strings are translated to Python spelling for context display.
  Lossy or non-literal previews appear in `# preview: ...` comments rather than assignments to invented values.
  This includes truncated containers and strings, tuples projected as lists, opaque objects, and non-finite floats.
  Display does not execute application equality, representation, annotation, or serialization code.
- Top-level Pydantic AI multimodal values are rendered as `name: TypeName  # attached content: ` followed by inline user content in the prompt payload.
    - This applies to top-level locals/globals entries.
- Top-level `list` / `tuple` values are also rendered as inline user content when every item is valid Pydantic AI `UserContent` and at least one item is multimodal.
    - The binding line still renders once, for example `photos: list  # attached content: `, followed by the original top-level item order.
    - Pure text-only sequences do not use this rule; they remain preview-rendered values.
- Explicit dotted references whose resolved leaf resolves to hoistable inline user content add a separate section line for that full dotted path (for example `holder.photo: BinaryContent  # attached content: ` followed by inline user content).
    - Only explicitly referenced dotted paths are added this way.
    - This is a leaf-only rendering rule, not recursive object-graph extraction.
    - Dotted-path resolution is attribute-oriented only. Each segment after the top-level name is resolved against discovered object fields using the same safe field sources as object capability rendering.
    - Dotted-path resolution does not treat `Mapping` keys as attribute segments. For mapping access, use Python expressions or helper functions (for example `nh_evaluate("payload['photo']")`) rather than `<payload.photo>`.
    - `list` / `tuple` leaves follow the same hoisting rule as top-level bindings: the sequence is hoisted when every item is valid Pydantic AI `UserContent` and at least one item is multimodal.
    - Non-multimodal dotted references remain preview text inside object capability rendering.
    - When a dotted multimodal reference is rendered as a separate section line, the corresponding field is omitted from the owning object capability block to avoid duplication. The field still consumes a slot in the `object_max_fields` budget.

Context is a bounded description, not executable initialization code.
Declaration spellings and native content need not form a complete Python program.

Ordering:

- Top-level entries follow the priority classes above and use lexicographic order within each class.
- Object methods and fields are rendered in lexicographic order by member name.
- Methods are rendered before fields for each object entry.
- If an item or token budget prevents rendering all top-level entries, a `# <snipped>` comment is appended at the end of the section.
  An adjacent omission comment names up to eight omitted entries and supplies `environment.discover()` and `environment.describe('name')` expressions using the live runtime reference.

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

`nighthawk.runtime.describe_value(value, cursor=None, max_items=8)` describes a known live value.
It is an optional ordinary Python function, available to a step only through an explicit import, argument, local binding, embedded input, or scoped task reference.
The runtime does not inject a value-description function into locals or globals, or unconditionally advertise one in the default prompt.
Explicitly supplied callables use the ordinary signature and documentation renderer, under the name selected by the program or host.
Python builtins and explicitly imported standard-library functions remain available through the same expression mechanism.
It does not infer a binding name, source, write eligibility, or declared contract from object identity.
Callable annotations belong to the value; named declaration annotations and output contracts belong to `StepEnvironment.describe(name, source="effective")`.
Presentation-only annotation spelling is not evaluated and does not weaken preparation-time output validation.

When entries are omitted, the default context advertises `environment.runtime_reference`, a generated collision-free Python root whose `.environment` exposes the current environment.
Application bindings, including unbound Python locals, retain their meaning.
The generated root cannot be used as a temporary assignment target and is excluded from explicit working-state snapshots.

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

1. **Binding functions** (Section 8.2): Callable values in step locals or step globals are rendered as text signatures in the prompt context. The LLM invokes them via `nh_evaluate`.
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
  Effective lookup prefers step writes, then Python names, then task references.
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

- `nh_evaluate(expression: str) -> object`
    - Evaluate a Python expression and return the result. Use to inspect values, call functions, and mutate objects in-place.
    - In-place mutations performed via `nh_evaluate` are not runtime-validated.
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

- Only names declared by `<:name>` bindings are eligible for commit.
- An executor may select a complete subset through `StepCandidate.binding_name_to_value`; omission or `UNSET` selects initialized declared outputs from the environment.
- Tracked assignment and mutation marks support working-state bookkeeping; they do not select which initialized outputs receive final validation.
- All selected outputs receive final validation; an initial `Raise` discards pending working values and rejects explicit nonempty output mappings.
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
    - The MCP tool-return transport carries only image (`BinaryContent.is_image`) and audio (`BinaryContent.is_audio`) items natively (as `mcp.types.ImageContent` / `mcp.types.AudioContent`). All other `BinaryContent` and `FileUrl` items, including document and video, project to `mcp.types.TextContent` so the transport stays symmetric with text-projected backends. Native MCP document and video carriage are unsupported. Multimodal-capable providers that bypass MCP still send supported items natively via `ToolReturnPart.files`.
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
    - These outcomes are valid only where Python permits `break` or `continue`, inside a `for`, `async for`, or `while` loop body. If requested outside a loop body, execution fails.
    - A loop's `else` clause is outside that loop's body. If nested within another loop body, these outcomes control the enclosing loop.
- `raise`:
    - Failure.
    - Payload keys: `kind`, `raise_message`, and optional `raise_error_type`.
    - A non-null `raise_error_type` MUST be one of the exception binding names listed in the prompt. Bindings may use aliases, such as `selected_error = ValueError`.
    - The structured output JSON Schema constrains this field to the allowed binding names or `null`, preserving the default even when a provider requires every field.
    - The adapter resolves the exception class through the common operation pipeline. An omitted or null selection uses `Exception`.
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

- Tool expression evaluation: if an `nh_evaluate` or `nh_assign` expression produces an awaitable, the host awaits it before returning the result to the model, including when the surrounding Natural function is synchronous.
- Return validation: if a `return` outcome's `return_expression` evaluates to an awaitable and the surrounding function is async, the host awaits it before return type validation. If the surrounding function is sync and the evaluated value is awaitable, execution fails with `ExecutionError`.
- Binding function calls: async binding functions called through `nh_evaluate` or `nh_assign` are auto-awaited in both sync and async Natural functions. The synchronous execution path bridges tool execution to an event loop; the awaitable-return restriction applies to the final `return_expression`, not to tool expressions.
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

- `run_id`: the Id of the active run. Every `run()` generates a fresh Id unless the caller supplies one; `scope()` retains it. Cross-process propagation and correlation belong to the host.
- `scope_id`: the Id of the current (possibly nested) run scope. This serves as the identity of the current logical execution context.
- `step_execution_id`: a fresh Id for each runtime invocation, including repeated loop iterations and concurrent calls. Executor retries retain this Id; executing the block again allocates another.
- `parent_step_execution_id`: the active parent step Id for correlation, or `None`; it does not transfer workspace objects across processes.
- `source_location`: the source label (`python_module:line`), shared by invocations of the same block. Both step fields are `None` outside active step execution.

Nighthawk does not own workspace filesystem concerns (such as include resolution or host file operations). Those concerns belong to the host application layer that embeds Nighthawk.

Working directory selection for provider backends is configured via `ModelSettings["working_directory"]` (absolute, resolved). When empty (default `""`), backends omit the working-directory option and use the provider default (typically the parent process current working directory).
API:

- `nighthawk.run(step_executor=UNSET, *, execution_model=UNSET, decision_model=UNSET, step_executor_configuration=UNSET, run_id=UNSET, usage_meter=UNSET)`
    - Omitted `step_executor` creates a managed `AgentStepExecutor`. Independent model selections or a full configuration replacement are resolved through the selected executor before entering the run.
    - Invalid run identifiers and usage meters fail before model resolution; rejected configuration shapes do not initialize a provider.
    - Validates the synchronous or asynchronous executor protocol before installing context; invalid executors raise `TypeError` and leave any parent context intact.
    - Replaces the current context step executor with the provided step executor.
    - Installs `usage_meter` as the run-level meter when given; omission or `UNSET` creates a fresh `UsageMeter`; `None` is invalid.
    - Resets scoped tools, capabilities, and task references to empty, oversight and lifecycle to `None`, and context construction to `DefaultContextBuilder` for the run.
    - Generates a new `ExecutionReference` for the duration of the `with`.
    - Uses a provided nonempty string `run_id`; omission or `UNSET` generates a new `run_id` (trace root). `None`, empty strings, and other types are invalid before context installation.
    - Always generates a fresh `scope_id`.
    - Can be used even when no step executor is currently set.
- `nighthawk.scope(...) -> Iterator[StepExecutor]`
    - Every configurable parameter defaults to `UNSET`; omission and explicit `UNSET` inherit.
    - `execution_model=...` and `decision_model=...` independently replace their models; `step_executor_configuration=...` replaces the full configuration, including defaults. Model selections cannot accompany full configuration replacement.
    - Ordinary values replace; empty sequences or mappings clear inherited collections.
    - `Extend(sequence)` appends system/user prompt suffix fragments, tools, or capabilities. Strings and bytes are invalid collection shapes. Repeated capability and fragment entries are preserved.
    - `Merge(mapping)` merges task references. Repeating the identical value is idempotent; a different value for the same name raises `NameConflictError` even if it compares equal. Wrappers capture collection structure without copying entries.
    - `oversight` and `lifecycle` accept `None`, clearing their hooks. Other fields reject it, as well as operation wrappers in unsupported positions.
    - Validate every argument shape before delegated model resolution, and resolve every field before installing context; failures leave the parent identity and settings intact. Require a run, create a fresh scope Id, and restore the parent after normal or exceptional exit.
    - Preserve the active step Id and source location inside a nested scope, so a child step remains correlated with the execution that called it.
    - `context_builder` replaces prompt construction; omission or `UNSET` inherits. See Section 8.2.4.
    - Explicit executor replacements use the same protocol validation as `run()`; invalid executors raise `TypeError` before context installation.
    - Executor replacement precedes model selection. Selection delegates to the active `ConfigurableStepExecutor`; direct `AgentStepExecutor` resolution respects managed/external ownership.
    - A replacement meter receives child usage without forwarding to the parent. Capabilities reach every Pydantic AI Agent request.
    - Yield the resolved executor. Nested runs reset scope defaults and restore their parent.
- `nighthawk.get_step_executor() -> StepExecutor`
    - Get the current step executor. Raises if unset.
- `nighthawk.get_execution_reference() -> ExecutionReference`
    - Get the current runtime execution identity. Raises if unset.

`UNSET` represents an omitted argument where the signature accepts it; the receiving API defines the default behavior.
Ordinary values select the complete value, including empty collections; `None` requires an explicitly supported absent or disabled value.
`Merge` and `Extend` request composition only in their supported scope fields.
Configuration value objects resolve their own defaults rather than storing unresolved `UNSET` fields.
These rules apply to the argument itself: `{"name": None}` supplies a value and never deletes the key.

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

Every executor returns a `StepCandidate` containing a resolved outcome and a `binding_name_to_value` selection, either `UNSET` or a shallow read-only mapping.
The runner requires this contract for custom executors and managed agents alike.
It checks allowed kinds, rejects undeclared selected output names, validates the selected output values, and independently validates a resolved return value.
Omission or `UNSET` selects initialized declared outputs at finalization, preferring tracked assignments over current Python values, including live globals and cells.
A supplied mapping is the complete selection: `{}` selects no writes, and a nonempty mapping receives no supplementation from the working environment.
Omitted names in an explicit selection receive neither final value validation nor writeback.
The standard agent executor omits its selection, preserving automatic validation of initialized declared outputs.
Unbound outputs and task-only references remain absent from automatic selection; a later Python read of an unbound output raises normally.
Assignment validation and finalization are separate boundaries and may each run validators.
For example, an initialized `Annotated[str, AfterValidator(str.strip)]` output containing `" draft "` finalizes as `"draft"` even without model assignment.
Selected output contracts are fixed at step entry.
A custom executor cannot change finalization contracts by replacing context metadata.

`StepCommit.outcome` uses the frozen Pydantic models exported from `nighthawk.runtime`: `Pass()`, `Return(value)`, `Break()`, `Continue()`, and `Raise(message, error_type=Exception)`.
`Return(None)` is distinct from `Pass()`.
`Raise.error_type` is a resolved exception class, validated before inspection.
An initial Raise selects no writes and discards pending working values without validating them.
`StepCandidate` and `Rewrite` both reject a resolved Raise paired with an explicit nonempty output mapping at construction.
Its commit mapping is empty.

`Oversight.inspect_step_commit` receives one validated commit and may return its decision directly or asynchronously.
`Accept` preserves validated values without repeating validators.
`AbortStep(reason)` produces a failed terminal record before generated Python assignments and propagates `StepAbortedError`.
Commit inspection accepts `Accept`, `Rewrite`, or `AbortStep`; `RejectOperation` is invalid at this boundary.
Commit refusal never reopens model inference.
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

`Oversight.inspect_operation(OperationRequest)` returns `OperationDecision = Accept | RejectOperation | AbortStep`, directly or asynchronously.
Evaluation, assignment, model return-expression resolution, native tools, and inspection operations share this boundary.
The request includes the execution reference, operation Id, kind, and applicable expression, assignment target, tool arguments, and expected type.
Admission runs before inspection; inspection runs before any operation body or await of its result.
`Accept` permits execution.
`RejectOperation(reason, guidance=None)` prevents that operation's body and produces an `OperationRejection` with the request and deliberately model-visible feedback.
Direct operation APIs raise `OperationRejectedError`, whose `rejection` exposes that same record; a custom executor may catch it and choose another operation in the same step.
`OperationRejection.model_feedback()` exposes status, operation and step identities, reason, guidance, and `evaluation_started=False`, without serializing live arguments or diagnostic exceptions.
Reasons must be nonempty strings; guidance is an optional string.
`AbortStep(reason)` prevents execution and ends the current step with `StepAbortedError`.
It does not cancel an enclosing run or prohibit a calling Python helper from deliberately handling the child's failure.
An uncaught child failure also fails its parent operation; it is not a new inspection decision about the parent.

Requests execute serially within each step: in emission order for an ordered model batch, and in admission order otherwise.
Pydantic AI capability hooks cannot reorder an ordered batch across this boundary.
Argument validation is included in that ordering.
After a recoverable rejection, the remaining calls in that model response are skipped before validation or execution, including output proposals and pending deferred calls.
Each skipped call receives an operation Id and an `OperationFinished(status="skipped", phase="admission", evaluation_started=False)` with `blocked_by_operation_id` referring to the refused operation.
Skipped output proposals use request kind `"output"`; an accepted candidate has no artificial output operation, but its expressions use the normal operation pipeline.
An earlier accepted candidate in the same response is discarded if a later operation is rejected; its already completed expression effects remain.
A subsequent model response makes new operation requests, each inspected normally.
Inside Nighthawk, Pydantic AI's `early` strategy follows emission order as `graceful` does, so an output cannot bypass an earlier proposed action.
`exhaustive` retains its output selection rules, subject to the rejection barrier and terminal limits.
Operation dispatch preserves Pydantic AI validators, retry budgets, and explicit deferral signals.
Pydantic AI runs outside that boundary use their own dispatch behavior.
Under `exhaustive`, upfront Pydantic AI function-call events have `args_valid=None` until the corresponding ordered validation runs; Nighthawk operation records retain the actual admission result.
Explicit Python concurrency inside a helper and separate child steps retain their own execution semantics.
Direct executor calls do not implicitly form a model-response batch; the executor owns their selection and ordering.

Recoverable rejection does not mark the step failed, restart it, roll back effects, repeat prior effects, or clear its working environment.
Operation identities differ across attempts; step identity and live application values remain intact.
Ordinary result validation, commit inspection, writeback, and one terminal delivery still apply.
A model's decision to finish after a rejection does not establish task correctness; application acceptance checks remain necessary.

`AbortStep`, inspection failures, operation delivery failures, and interruptions remain terminal for that step.
Later queued operations cannot execute their bodies, and an executor cannot suppress a recorded terminal failure and finalize a successful candidate.
Only a normally returned `RejectOperation` decision authorizes recoverable oversight feedback.
An inspector raising `OperationRejectedError`, `OperationError`, or `ModelRetry` has failed; exception type alone never makes that inspection recoverable.
Ordinary exceptions from application helpers and native tools propagate to Python.
Runtime syntax, name-resolution, and value-validation errors provide recoverable operation feedback; Pydantic AI control signals retain their native meaning.
Classify failures by origin, not exception class: `NameError` or `ValueError` from a helper body remains an application exception.
An uncaught child Natural exception preserves its child terminal event and also fails its parent operation.
Return expected domain failures as ordinary helper results when the model should act on them; Python helpers may catch specific domain exceptions to construct those results.
Preserve explicit `ModelRetry`, `CallDeferred`, and `ApprovalRequired` signals through Pydantic AI machinery, and distinguish Pydantic validation failures from propagated validator exceptions.
Each completed operation delivers an `OperationFinished` fact with its phase, whether evaluation started, and its live result or original exception.
Statuses are `succeeded`, `rejected`, `aborted`, `skipped`, `failed`, and `interrupted`.
Recoverable rejection includes `rejection`; explicit `AbortStep` produces `aborted` and its original `StepAbortedError`.
A notification failure does not change that fact or replay the operation.
`OperationDeliveryError.finished` preserves the refusal or skipped-operation fact, and `delivery_cause` preserves the notification failure.
Notification failure is never ordinary model feedback, even if the callback raises a retry or rejection exception.
The existing interruption and cleanup responsibilities continue to apply.

Pydantic AI validates native and provided tool arguments before entering the tool body boundary.
A failed schema or argument-validator admission receives an operation Id and an `OperationFinished` record with `phase="admission"` and `evaluation_started=False`, retaining the original exception and unvalidated arguments.
Malformed JSON arguments are retained under `unvalidated_arguments` in the request's argument mapping.
Validation errors remain recoverable through Pydantic AI; ordinary exceptions propagated by application validators terminate the step unchanged.
If a capability repairs invalid arguments, the failed admission remains recorded and the repaired request enters a separately identified inspected operation; the tool body runs once.
Approval and deferral during argument validation remain in Pydantic AI's designated machinery and do not fabricate a failed admission.

Standard Pydantic AI model adapters resolve candidate expressions through this pipeline inside output processing, before leaving the model loop.
Recoverable refusal becomes model-visible feedback, allowing a corrected expression in the same agent run and step.
Resolved candidates carry Python values to the runner; finalization never reevaluates their expressions.
Function-tool refusals use denied tool returns, distinct from successful values, and include structured operation correlation.
MCP and SDK transports set their error indicator and retain the canonical refusal feedback without lossy preview truncation.
Coding-agent transports expose subsequent individual tool requests, but do not expose the provider's internal response batches; they cannot promise that invisible sibling calls are skipped.
They do not silently convert recoverable refusal into a terminal error.
Protocol agents supplied with `from_agent` that do not execute Pydantic AI capabilities own candidate-recovery integration; an unhandled rejection propagates instead of triggering automatic replay.
Async Natural returns may await the expression result once; sync Natural returns reject awaitable expression results.
Custom executors may directly construct `Return(value)` without evaluating an expression or creating an operation event.
The runner independently validates the resolved value and all selected outputs before commit inspection.
Expression-syntax policies belong in operation inspection; policies relating return values to committed outputs belong in commit inspection.
A bindings-only rewrite can intentionally diverge from an earlier return value.

Correction requests retain the same request admission hooks, usage accounting, retry limits, and active configuration.
Function-tool refusals consume ordinary requests and tool-call usage; output-expression refusals also consume the existing output retry budget.
Limits and host stop decisions cannot be overridden by another candidate from the same response.
No refusal-specific budget or universal termination guarantee is added for unlimited configurations or arbitrary custom executors.
The request boundary is the Core/Pydantic AI-visible request; internal coding-agent inference calls retain their backend-specific controls.
`UsageMeter` aggregates completed agent runs and is not an in-progress per-request gate; use the request capability boundary for that purpose.
See [operation feedback](operation-feedback.md) for executable examples and the backend support matrix.

### 10.4. Terminal delivery and host ledgers

`nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=callback))` installs a callback accepting `StepFinished` and returning `None` or an awaitable completion.
`on_step_started` runs before preparation, and `on_operation_finished` delivers operation facts.
All three callbacks support direct and asynchronous completion. Omission or `UNSET` inherits; `None` clears. A new run resets lifecycle. Each execution captures the configuration on entry, before preparation. If that configuration has a handler, core attempts notification once while Python can unwind normally; a cleared or absent handler receives no notification. Definition-time parsing, decorator compilation, and failure to enter a required run are outside this boundary. Identity, step context when established, and the step span remain active during delivery and restore afterward, including when delivery fails.

The public `nh.get_lifecycle()` returns the currently scoped configuration object, or `None`, and requires an active run. Hosts can compare it by identity with their installed StepLifecycle at a host-controlled admission boundary. The getter observes configuration; it neither prevents `scope(lifecycle=None)` or nested-run resets nor guarantees later checks use the captured configuration of an already-started step. When a required lifecycle is absent, the host must record any mediation-bypass failure at a boundary that remains active, not rely on the removed callback. Core wraps preparation and finalization failures when no handler translates them; application exceptions recorded by the operation pipeline propagate unchanged.

Generated Python resolves the candidate, performs each selected write assignment, records each completed assignment, delivers the terminal record, and then dispatches return/break/continue. A prepared DSL exception is dispatched after delivery as well. Callback failure prevents further control dispatch but does not undo completed assignments, object mutation, tools, validators, or expression side effects. Partial assignment failures report only writes whose assignments completed. Rejected commits perform no generated assignments.

`StepFinished` is the discriminated union of frozen `StepCompleted`, `StepRaised`, `StepFailed`, and `StepInterrupted`, with respective `kind` values `completed`, `raised`, `failed`, and `interrupted`.
All carry `execution_reference`, `block_kind`, `model_result`, `processed_natural_program`, `input_binding_name_to_value`, `allowed_outcome_kinds`, and `assigned_binding_name_to_value`.
The declared `block_kind` is known on entry, including failures or interruptions in started delivery, f-string rendering, and embedded argument validation before preparation.
Preparation-dependent fields are `None` when not established; empty mappings or tuples mean known empty values.
`model_result` is absent until a managed model run returns its native result.
Runtime references require both step identity fields.
Mapping and tuple structure is snapshotted; contained Python objects retain their exact types and identity.
Hosts must serialize or copy deliberately for durable records.
The processed Natural program and execution identity do not identify the complete program revision: helper implementations, declarations, and dependency versions are not snapshotted as a revision record.
See [Record revision evidence](embedded-execution.md#record-revision-evidence) for host records that supplement this execution evidence.

`StepCompleted.outcome` is the final Pass/Return/Break/Continue. `StepRaised` holds a final Raise and its successfully constructed `exception`, including domain exceptions subclassing NighthawkError. Model-selected exception expressions use the operation pipeline; exception constructor failures are `StepFailed` at `raise_construction`, not domain raises. `StepFailed` holds `original_exception`, `failure_stage`, optional `attempted_outcome`, and optional `validated_binding_name_to_value`. An explicit `AbortStep` decision about this step additionally carries `inspection_subject` and `abort_reason`. Invalid hook decisions and exceptions raised by inspectors remain inspection failures.

`FailureStage` identifies preparation, started_delivery, operation_inspection, operation_execution, operation_delivery, executor, outcome_validation, binding_validation, return_await, return_validation, commit_inspection, rewrite_validation, oversight_abort, raise_construction, or binding_assignment.
Classification uses structured runtime state, not exception text.
Terminal failures recorded by the operation pipeline and explicit commit `AbortStep` decisions propagate unchanged; other internal execution failures use `ExecutionError(step_failed)` chained from the original exception unless delivery supplies a host exception.
DSL Raise propagates its prepared exception.

The callback may append the record to a host-owned authoritative ledger and raise a public host exception containing that exact stored event. For StepFailed, core explicitly chains the callback exception from `original_exception`; for StepRaised, from the prepared domain exception. If the callback rethrows the exact original exception object, core preserves its existing cause rather than chaining the exception to itself. Arbitrary callback exceptions are preserved and never reclassified or redelivered. `StepDeliveryError(execution_reference, cause)` is available for host adapters reporting storage failure; its `delivery_cause` does not imply a durable event exists. During ordinary execution failure, the outward chain prioritizes the execution exception and the storage cause remains available in `delivery_cause`.

Executor and commit-inspector exceptions produce StepFailed delivery even when the original exception is already a host-defined NaturalExecutionError. Core does not recognize host exception types or deduplicate host records. A handler that finds an already-recorded host exception must verify both matching execution identity and association with the exact stored event before skipping another append, then rethrow that exception to preserve its type. Returning normally instead causes core to raise ExecutionError chained from the host exception. A nested execution has a different step_execution_id and needs its own terminal event even when its failure carries a recorded child exception. Hosts should not suppress every exception of their public type indiscriminately.

On StepCompleted delivery failure, completed assignments remain visible and the callback's exception propagates. There is no original execution failure to fabricate as its cause, and the already-classified StepCompleted is not rewritten into StepFailed. Hosts may classify the overall operation as a commit/delivery failure and record their own anomaly; that classification is separate from core's execution result and does not authorize retrying the execution.

`StepInterrupted` holds the active `failure_stage` and original cancellation or process-control `BaseException`.
An interrupted asynchronous notification receives a shielded attempt until `StepLifecycle.interruption_delivery_timeout`, which requests cancellation.
Core then waits until the notification and its cancellation cleanup actually finish.
The synchronous bridge propagates caller interruption cooperatively and joins worker processing, notifications, asynchronous generators, and owned loop shutdown before returning or raising.
This includes interruption while `Thread.start()` waits for an already launched worker to initialize.
Asynchronous generator shutdown errors are propagated or chained to the original interruption, including when asyncio reports them through its loop exception handler.
A cancellation-resistant handler can delay completion indefinitely; neither timeout nor thread status proves completion.
Repeated caller interruptions do not abandon cleanup or repeatedly cancel the same finalizer.
If a notification is already handling its own cancellation, caller interruption or the delivery timeout joins that cleanup without cancelling it again.
The bridge likewise preserves existing cancellation cleanup in its root task, checking on that task's event loop when delivering the caller's cancellation request.
An asynchronous caller likewise waits for owned notifications, while retaining ownership of its loop and unrelated tasks.
The first interruption retains its identity and preexisting cause; additional cleanup failures are explicitly chained, using a `BaseExceptionGroup` when necessary.
Cancellation, SystemExit, KeyboardInterrupt, and GeneratorExit are not translated into ordinary host execution failures.

The guarantee is one in-process delivery attempt, not a transaction across Python and storage. A callback that appends and then raises cannot be distinguished automatically from an uncertain append failure. Hosts deduplicate and retry storage by `step_execution_id`, never by replaying a Natural execution. Hard process termination and unavailable storage cannot guarantee durable delivery. OpenTelemetry is optional diagnostics, not the authoritative ledger. Terminal tracing follows the typed classification: await failures receive `nighthawk.step.failed` and exception/error status; interruptions receive `nighthawk.step.interrupted`; delivery failures may add `nighthawk.step.delivery_failed` without a second execution terminal event.

Host integrations can separate responsibilities: the executor boundary admits and accounts for execution, `inspect_step_commit` approves or rewrites the resolved candidate before assignment, and StepLifecycle records the terminal result and translates failures. Preparation can fail before executor admission; terminal consumers must accept records without a matching admission event. The shared `execution_reference.step_execution_id` correlates executor StepContext, inspectors, and terminal records without storing a live StepContext or pending-commit ContextVar. Terminal records make type-erasing validation workarounds unnecessary for catching binding, expression, and return-validation failures, but do not remove the host's other validation requirements. No external host code or host-specific serialization schema is provided by core. The deterministic ledger fixture in `tests/lifecycle/test_step_ledger.py` demonstrates recording even when Python inside the Natural function catches the translated host exception.

#### 10.4.1. Delivery failure cases

| Delivery failure | Execution fact and boundary behavior |
| --- | --- |
| `on_step_started` fails | No preparation or executor body starts. Attempt terminal delivery with `StepFailed` identifying started delivery as the failure stage. |
| An operation succeeds, then `on_operation_finished` fails | Preserve the successful `OperationFinished` fact and its effects. Stop queued operations and finalization; attempt `StepFailed` terminal delivery identifying operation delivery as the failure stage. |
| An operation fails, then its notification also fails | Retain both the operation failure and notification failure. Stop the step and attempt terminal delivery; do not convert the notification error into recoverable model feedback. |
| Caller writes succeed, then `on_step_finished(StepCompleted)` fails | Keep the writes and completed record. Propagate delivery failure without emitting `StepFailed` or executing again. |
| Terminal delivery of `StepRaised` or `StepFailed` fails | Preserve the original execution record and cause alongside delivery failure; do not emit another terminal event. |

The terminal attempt precedes any Python `except` outside the Natural boundary.
`StepLifecycle.interruption_delivery_timeout` defaults to one second before requesting cancellation of interrupted asynchronous delivery; it does not bound total cleanup time, a blocking synchronous hook, or guarantee storage.
Cancellation while approval is pending does not start the operation.
Cancellation after execution starts leaves possible external effects for host reconciliation.
Host domain operations should validate, approve, execute, and record their own concrete requests whether invoked by Python or Natural; core hooks add intervention points without replacing that authority.

## 11. Interpolation (opt-in, f-strings only)

### 11.1. Rationale

Natural blocks can compose trusted program fragments and host-authorized configuration through explicit Python f-string syntax.
Interpolated values become program text under the [trust contract](#3-hard-constraints); task data is supplied through bindings.

### 11.2. Mechanism

- Docstring Natural blocks are always literal. They are never interpolated.
- Interpolated Natural blocks are inline f-string Natural blocks (standalone f-string expression statements).
- Interpolation follows standard Python f-string semantics.
    - Expression evaluation rules are those of Python.
    - Brace escaping uses `{{` and `}}` in the f-string source to produce literal `{` and `}` in the rendered text.
- Headers and binding declarations are static.
  An unescaped angle-bracket marker cannot span an interpolated expression; write the complete declaration in a literal segment.
  Escaped literal markers may contain interpolated text.
- A decision instruction may be supplied entirely through interpolation.
  Its rendered body must still be nonempty and satisfy the decision-block restrictions before executor admission.

Note:

- This interpolation mechanism is distinct from the `nh_evaluate` tool. f-string evaluation runs in the normal Python execution context, while `nh_evaluate` evaluates expressions inside the Natural execution environment (`StepEnvironment`).

Decision:

- Any Python expression is permitted inside f-string `{...}` segments under the trusted-input model.
- There is no implicit placeholder replacement or template preprocessing step for Natural blocks.

## 12. Persistence and user-managed state

Nighthawk does not define a built-in persistence or memory model.

If you want a long-lived object, define it yourself and expose it as an ordinary Python value through lexical state or scoped task references. Runtime expressions can read it and `nh_evaluate` can mutate it in place. Sharing a reference does not provide durable persistence.

### 12.1. Carry pattern

The carry pattern is an idiomatic use of read bindings for cross-block context continuity. Pass a mutable Python object (e.g., `list[str]`) as a read binding (`<carry>`) and instruct the LLM to mutate it in place via `nh_evaluate`. Rebinding an existing Python name requires an output declaration, so a read-only declaration preserves the caller's name association while allowing object mutations.

For practical examples and design tips, see [Patterns](patterns.md#cross-block-composition).

## 13. Error handling

Nighthawk execution, policy, and delivery failures derive from `NighthawkError`.
Recoverable operation diagnostics use `OperationError`, a `ToolBoundaryError` subtype, so custom executors can handle them at the operation boundary.
Invalid arguments can raise ordinary `TypeError`, `ValueError`, or Pydantic validation errors.

Exception hierarchy:

- `NighthawkError`: Base class for Nighthawk execution, policy, and delivery failures.
    - Raised when: runtime preconditions fail (e.g. no active run context, missing step executor).
- `NaturalParseError(NighthawkError)`: Natural block parsing or frontmatter parsing failed.
    - Raised when: the sentinel is missing, bindings are invalid, frontmatter YAML is malformed, or AST extraction fails.
- `ExecutionError(NighthawkError)`: runtime internal failure; `step_failed` holds its `StepFailed` record and `__cause__` holds the original exception.
    - Raised when: the LLM returns invalid JSON, an outcome kind is disallowed, or boundary value validation fails outside the operation pipeline.
    - A DSL Raise defaults to ordinary `Exception`; application operation failures retain their own exception types.
- `ToolBoundaryError(Exception)`: Internal tool boundary signal with an `ErrorKind` and optional guidance. A failed `nh_assign` value validation raises this signal with `kind="invalid_input"`; the tool wrapper converts it into `ToolOutcome.error` for the model. It is not a `NighthawkError` subclass.
- `ToolDeclarationError(NighthawkError)`: Invalid tool declaration.
- `NameConflictError(NighthawkError)`: Distinct declarations claim the same name.
- `ToolNameConflictError(ToolDeclarationError, NameConflictError)`: Tool name collision, including reserved built-in names.
    - Raised when: a tool name is invalid, collides with a built-in tool, or is declared twice within the visible scope.
- `CircuitOpenError(NighthawkError)`: The resilience circuit breaker refuses a call while open; the protected function does not run.
- `BudgetExceededError(NighthawkError)`: A resilience usage check refuses or reports work beyond its configured limits; earlier effects remain.

Exceptions propagated to the host can be caught with standard `try`/`except`. Recoverable tool failures are instead returned to the model as structured observations, as specified in [Section 8.3](#83-tools-available-to-the-llm). Internal step failures follow the `ExecutionError` and terminal-delivery rules in [Section 10.4](#104-terminal-delivery-and-host-ledgers).

## 14. Step executor

### 14.1. Protocols

Nighthawk defines two step executor protocols. Both are `@runtime_checkable`.

- `SyncStepExecutor`
    - `run_step(*, request: StepRequest) -> StepCandidate`
- `AsyncStepExecutor`
    - `run_step_async(*, request: StepRequest) -> StepCandidate`

The type alias `StepExecutor = SyncStepExecutor | AsyncStepExecutor` is the union accepted by `nighthawk.run()`.
Executor protocols, requests, candidates, resolved outcomes, and runtime operations are available from `nighthawk.runtime`.
Its public exports participate in ordinary Python `dir()` and attribute inspection without requiring an active run.

`StepRequest` holds `block_kind`, `input_binding_name_to_value`, `execution_reference`, `processed_natural_program`, `step_context`, ordered `output_binding_names`, `binding_name_to_type`, `return_annotation`, `allowed_outcome_kinds`, and `allow_awaitable_return`.
Its context owns the environment; no second namespace is assembled for the executor.
Both methods return `StepCandidate(outcome=..., binding_name_to_value=...)` containing live values.
`StepRequest.snapshot_output_bindings()` returns an independent dictionary of initialized declared outputs in declaration order using the automatic-selection rule.
It preserves value identity, excludes unbound outputs and task-only references, and neither validates nor writes values.
The snapshot fixes name associations when called; an omitted candidate selection reads them later, during finalization.
Compose selected snapshots and replacements with ordinary Python dictionaries when needed.
Finalization remains mandatory, as specified in Section 10.2.

### 14.2. AgentStepExecutor

`AgentStepExecutor` is the built-in implementation that delegates Natural block execution to a Pydantic AI agent. It implements both `SyncStepExecutor` and `AsyncStepExecutor`.

Construction paths:

- `AgentStepExecutor(configuration: StepExecutorConfiguration | UnsetType = UNSET) -> AgentStepExecutor`
    - Creates an executor managing its execution agent from configuration; an agent is constructed when an execution model is selected.
- `AgentStepExecutor.from_agent(*, agent: StepExecutionAgent, configuration: StepExecutorConfiguration | UnsetType = UNSET) -> AgentStepExecutor`
    - Borrows an existing agent for `execute` without constructing a replacement. The agent owns execution model selection and is not rebuilt on configuration changes.

Both paths use `decision_model` independently for `decide`.
Omission or `UNSET` creates a fresh `StepExecutorConfiguration()`.
`None` and raw dictionaries are invalid before agent construction; parse data explicitly with `StepExecutorConfiguration.model_validate(data)`.

Instance attributes:

- `configuration: StepExecutorConfiguration` — the resolved configuration.
- `agent_is_managed: bool` — `True` when the agent was built internally from the configuration, `False` when provided externally via `from_agent`.
- `token_encoding` — tiktoken encoding resolved from the configuration.
- `tool_result_rendering_policy: ToolResultRenderingPolicy` — policy for rendering tool result previews, derived from configuration.

### 14.3. Custom backends

Any object implementing `SyncStepExecutor` or `AsyncStepExecutor` can serve as a backend. The protocol surface for `AsyncStepExecutor`:

```py
from nighthawk.runtime import AsyncStepExecutor, StepCandidate, StepRequest


class MyExecutor(AsyncStepExecutor):
    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        # Read the program and selected contracts from request.
        # Use request.step_context.environment for live name resolution.
        # Runtime evaluate_expression_async and assign_expression_async use the common
        # operation pipeline, including inspection and lifecycle delivery.
        # Return resolved live values; the runner validates the final candidate.
        ...
```

`SyncStepExecutor` follows the same shape with `run_step` instead of `run_step_async`.

For most custom backends, wrapping a Pydantic AI `Agent` via `AgentStepExecutor.from_agent` (see [Executors](executors.md#custom-backends)) is simpler than implementing the protocol directly. Direct implementation is appropriate when the backend does not use a Pydantic AI agent at all.

See the [API reference](api.md#runtime-executor-boundary) for the protocol definitions.

### 14.4. Nested execution acceptance example

This is a proof example for nested Natural execution with explicit inputs. Its fixed counter and labels isolate the semantics; they are not intended as a useful application by themselves. `block_two` receives its iteration through a Python parameter.

```python
import nighthawk as nh


@nh.function
async def block_two(i: int) -> str:
    message = ""
    """execute
    Inspect <i>, the explicitly supplied one-based iteration.
    Set <:message> to 'fifth' on iteration 5, 'eighth' on iteration 8,
    and 'ordinary' on every other iteration.
    """
    return message


@nh.function
async def block_one() -> list[str]:
    messages: list[str] = []
    """execute
    Call <block_two> ten times in sequence.
    Before each call, use nh_assign to set the temporary name i to the
    current one-based iteration, from 1 through 10.
    Pass i as the argument to block_two(i).
    Collect the returned strings in <:messages> in call order.
    """
    return messages
```

The executor materializes `i` and passes it to the child; mentioning a counter in prose does not create runtime state. The fifth and eighth calls receive 5 and 8. Later parent assignment does not change an already supplied argument.

Call `await block_one()` inside `nh.run(executor)`.
The expected successful result is four `ordinary` entries, `fifth`, two `ordinary` entries, `eighth`, and two `ordinary` entries.
Deterministic executor tests establish the environment mechanics; model evaluations measure whether an LLM follows the ten-call instruction.
The runtime must not claim that type validation of `list[str]` proves the count or contents.

Custom executors materialize working names using `assign_expression_async` and call children with explicit arguments using `evaluate_expression_async`, both exported from `nighthawk.runtime`.
Their synchronous counterparts are `assign_expression` and `evaluate_expression`.
These entry points include admission, inspection, ordering, workspace updates, and lifecycle delivery; do not mutate environment views or build a second operation policy.
Directly constructing a `StepCandidate` proposes final values but creates no intermediate operation or shared temporary state.

### 14.5. Behavioral acceptance cases

The contracts above own these semantics; this table collects review cases for implementations and adapters.

| Scenario | Required result |
| --- | --- |
| Child Python parameter `i=99`, parent `i=5`, injected `i=8` | Bare `i` is 99; explicit task access is 8; there is no parent name source. |
| Child module global `i=99`, parent `i=5` | Bare `i` is 99. |
| Child Python local `i` is unbound, parent `i=5` | `UnboundLocalError`; no fallback. |
| Child local `i=None`, parent `i=5` | Resolve `None`. |
| No lexical `i`, parent `i=5`, injected `i=8` | Resolve 8; without injection, raise `NameError`. |
| Child declares a read that exists only in the parent | Fail preparation with the original lookup error, without executor admission or output writeback; deliver the ordinary failed-step lifecycle fact. |
| Inner scope clears task references | Cleared injection cannot reappear from a parent's working names or an older ancestor. |
| A Python helper selects a parent's value for `task_references` | The child sees that explicit reference with task provenance and the same object identity. |
| A parent opens a sharing scope before calling a child | The child keeps the parent step and call identities, executor, inspection, and lifecycle relationships. |
| Visible local `value=7` evaluated as `(lambda: value)()` | Return 7 using the same environment as direct lookup. |
| Visible local `value=7` evaluated as `list(value + i for i in range(2))` | Return `[7, 8]`; the generator is consumed within one operation. |
| Existing closure cell is rebound before the Natural call, or an explicit global/nonlocal output is committed | Observe the latest cell value, or update the declared module/cell destination; do not use a captured value copy. |
| `nh_evaluate` receives `(value := 8)` | Reject the named assignment and direct the model to tracked assignment. |
| `nh_assign` targets an existing Python name not declared as an output, and its right-hand side calls a helper | Reject before invoking the helper; a new eligible temporary name is allowed. |
| An initialized declared output is never assigned and the candidate omits its selection | Include it in non-Raise finalization; its validators may transform it or reject it. |
| An executor explicitly supplies `{}` as its output selection | No final output value validation or writeback; do not supplement initial or tracked values. |
| An executor supplies only one of several initialized outputs | Validate and inspect only that output; leave the other caller associations unchanged by writeback. |
| A host composes `request.snapshot_output_bindings()` with replacements | Use those complete associations; later environment rebindings do not alter the explicit selection. |
| Local `a: int = 0` is declared as `<:a>` and receives `'invalid'` through `nh_assign` | Reject at assignment validation and retain its previous working value; final output validation remains a separate boundary. |
| A Pydantic model with `validate_assignment=True` has `confidence: float = Field(ge=0.0, le=1.0)` and receives `2.0` | Reject through the model-aware assignment path; the plain `float` annotation alone is insufficient. |
| A dotted target is an annotated ordinary object attribute or a model field with assignment validation disabled | Report an unsupported target and recommend a Python helper. Report any earlier right-hand-side effects. |
| An eligible assignment's helper mutates a list, then its return fails validation | The pending name assignment fails; the list mutation remains. |
| An admitted dotted assignment has an intermediate getter that fails | The right-hand side has already run once; its effects remain, and no final setter runs. |
| One model batch assigns the parent temporary `i=5`, then invokes `child(i)` | Run in that order; the child receives the argument 5. Awaited approval does not allow the child call to overtake the assignment. |
| Child assigns a temporary with the same name as a parent's working value | Parent name is unchanged; child lookup uses its own working value. |
| Child mutates a list shared through an argument or task reference | Parent observes the same list mutation. |
| Host rejects the final candidate after an earlier approved operation | No pending caller assignments; the earlier operation remains recorded and effective. |
| Host awaits approval and rejects | Operation body is not invoked; rejection has a correlated record. |
| Host rewrites the output mapping to `{}` | No caller name assignments; workspace values are not automatically added back. |
| Host supplies `Rewrite(outcome=Return(None))` | Validate `None` against the return contract; retain the candidate's output mapping. |
| Initial `Raise` has invalid pending output values | Skip final validation of discarded values and inspect an empty output mapping. |
| A candidate or rewrite explicitly pairs `Raise` with nonempty outputs | Reject the combination at construction. |
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


## Explicit embedded steps

`nighthawk.runtime.StepDefinition` holds a trusted Natural program, read and ordered write declarations, output and return annotations, loop context, and source location.
Definitions are immutable and copy their declaration sequences and annotation mapping, retaining annotation object identity.
Declarations must be ordered sequences of unique ASCII Python identifiers, excluding keywords; read and write declarations may overlap.
Malformed declarations or annotations for undeclared outputs raise `BindingDeclarationError` at construction, outside any execution lifecycle.
`execute_step(definition, *, name_to_value, write_binding)` and its asynchronous counterpart execute one invocation using the explicit namespace and required `write_binding(name, value)` callback.
Definitions retain no invocation state and may be reused with different namespaces, initial values, types, and writers; annotation resolution and inference occur on every invocation.
Hosts may construct new definitions from authorized Natural text at runtime; these entry points do not inspect Python function source.
They require an active run and use its configured executor and the ordinary preparation, operation, validation, commit-inspection, and lifecycle machinery.
The program is a body without a block header. `block_kind="execute"` is the default and supports deny frontmatter; `block_kind="decide"` requires exactly one output and a judgment instruction.
Declarations are authoritative host metadata; these entry points do not parse bindings from text, expand includes, or interpolate source.
The embedded entry points do not invoke `FunctionCallHandler`; installing `function_call_scope` does not admit or refuse an embedded step.
Hosts must authorize embedded programs through an application-controlled entry point before preparation and execution.
The ordinary operation, commit-inspection, and lifecycle boundaries do not replace that authorization.
The [embedded execution guide](embedded-execution.md) provides complete examples and the public API contract.

The mapping is shallow-snapshotted during preparation, including when it is empty.
Declared reads must be present in that mapping; builtins, task references, caller frames, and parent working state cannot supply a missing declared read.
Effective expression lookup uses tracked step assignments, explicit Python names, builtins, then scoped task references.
Prompt context, discovery, evaluation, type references, and model-selected exception expressions use that same environment, without caller locals, globals, closure cells, or parent working-state fallback.
Called Python functions retain their own globals and closures.
Called Natural functions establish their own lexical environment and receive the active scope's task references.
The explicit parent namespace and its later temporaries are not implicit inputs to those functions or their descendants.
Use arguments or an ordinary Python helper with `scope(task_references=...)` to share selected values.
Explicit environment selection does not change scoped execution configuration or establish an isolation boundary.

Output and return annotations, including string and nested forward references, resolve during preparation in the selected environment.
Missing output annotations use the inference rules in Section 7; explicit `object`, `Any`, and `None` keep their meanings.
The ordinary output-selection rules in Section 10.2 remain in effect, including initialized outputs, Raise discarding pending values, and replacement rewrites.
No initialized value is recorded as assigned merely because it exists or was selected for validation.

Writeback calls the host writer sequentially in declaration order after commit inspection.
Core awaits an awaitable writer result and records the assignment only after normal completion.
The supplied namespace is not automatically updated.
A writer must apply the selected value before normal return; partial effects from a writer that raises remain the host's responsibility.
Previously completed writes survive subsequent failure or cancellation and appear in the same step's terminal record.
The normal terminal-delivery contract, including single delivery attempt, interruption classification, and preservation of writes on notification failure, applies.
After successful terminal delivery, the entry point returns Pass/Return/Break/Continue for host dispatch; a final Raise propagates its prepared exception.
The optional `source_location` supplies a program label, defaulting to `<embedded>:0`, without selecting a Python frame.

`infer_binding_types` and `validate_output_bindings`, exported from `nighthawk.runtime`, are the same binding-processing functions used by ordinary blocks.
Both return new mappings and leave their input mappings unchanged.
The public `MISSING_ANNOTATION` marker distinguishes an existing unannotated declaration from explicit Python types.
Inference processes only supplied declarations and uses runtime container types without inferring type arguments.
Independent validation accepts resolved annotations and an ordered sequence of unique binding names.
It rejects invalid declarations, undeclared candidates, and annotations for undeclared outputs with `BindingDeclarationError` before individual validation.
It visits supplied candidates in declaration order.
Missing annotations skip individual validation; explicit None does not.
Independent validation neither supplements initial values nor assigns or records outputs.
Value validation raises `BindingValidationError` with the binding name and original validation exception; ordinary step failures retain that original exception and binding-specific diagnostics.
`BindingDeclarationError` and `BindingValidationError` derive from `BindingError`, which derives from `NighthawkError`.
Independent binding helpers do not produce execution lifecycle records or raise `ExecutionError`.
Neither helper promises deep copies or rollback of validator effects.

## Embedded execution boundaries

Python owns task decomposition, execution units, approval policy, and recovery.
Core supplies optional call interception, exact transformation correspondence, context propagation, and completion of its owned work.

| Boundary | Owner | Entry | Successful exit | Refusal | Interruption | Delivery failure |
| --- | --- | --- | --- | --- | --- | --- |
| Function | Core wrapper; host admission and execution unit | Handler before model resolution and Python body | Body, Python finally, explicitly joined children, and owned processing ended; model scope restored | Handler raises before calling continuation; no body effects or provider initialization | Propagates after owned cleanup; completion does not imply success or rollback | Handler sees eventual exception; a successful block fact can coexist with a failed function |
| Natural block | Core preparation, operations, validation, writeback, notifications | Started notification before preparation | Selected assignments and validation completed, then one terminal attempt ended | Operation or commit rejection follows its existing boundary | StepInterrupted retains primary interruption; notification cleanup is joined | Already established terminal fact and writes remain; no redelivery or replay |
| Model request | Executor transport; host capability observation and admission | Capability-visible model request | Available response and usage observed before next sequential admission | Host raises before forwarding request | Wrapping capability can distinguish BaseException from success | Host owns records and deduplication; SDK retries and coding-agent internal calls may be invisible |

### Whole-function interception and exact definitions

`nh.function_call_scope(handler)` installs a synchronous/asynchronous `FunctionCallHandler` independently of `nh.run()`.
Omission or `UNSET` inherits, `None` clears interception, and an explicit handler replaces it.
Validate both callable handler methods before installing context; invalid values leave the parent handler intact.
`None` explicitly clears it until scope exit.
A handler can accept a call and establish a run before invoking `proceed()`.
Core reads the current handler when the wrapper executes, so aliases created before installation still enter the boundary.
Async calls use the context in which the coroutine runs; a Task uses its selected creation context rather than a later waiter's context.
Argument expressions and unrelated outer decorators execute outside this boundary.
Arguments retain object identity, and inspection never evaluates deferred annotations.

`FunctionCall` exposes `call_id`, `parent_call_id`, `definition`, `arguments`, the immutable shallow `keyword_name_to_value` mapping.
`nh.get_function_call()` returns the current invocation or `None`.
Every intercepted nested call gets its own identity; Core does not assign host execution units or implicitly start another run.
Handler ordering is entry, body and owned processing, then handler return or failure. The body inherits its caller's active scope.

The continuation may start once, only while its handler is active and in that same Task and thread.
A normal handler return without completing its continuation raises `FunctionCallError`; refusal raises the host's own exception before proceeding.
After starting, ordinary host exception handling is allowed and Core never retries the body.
Async handlers must await their continuation; Core closes its own unstarted continuation coroutines when a handler discards them.
A synchronous function returning a coroutine returns that exact object without implicit await or close.
Intercepted generator and async-generator functions raise `FunctionCallError` before body execution because iterator creation is not completion of their lifetime.
Detached application tasks, unjoined threads, external service work, hard process termination, and malicious internal mutation are outside this guarantee.

`nh.get_function_definition(value)` returns a frozen `FunctionDefinition` only for a registered public wrapper or its standard bound-method, staticmethod, or classmethod descriptor.
Its `original_function`, `function`, and `transformed_function` fields are the actual objects.
Copied attributes, raw source/body functions, and unrelated outer wrappers are not exact registration evidence.
The registry uses weak ownership; a snapshot held by the application intentionally retains its functions.
`get_transformed_function()` follows conventional wrapper chains for inspection.

### Model declarations and observation

`nh.run(execution_model=..., decision_model=...)` creates a managed executor when none is supplied.
`nh.scope(execution_model=..., decision_model=...)` replaces either model independently, retaining other configuration fields.
Omission or `UNSET` inherits each selection; `None` clears it, causing that block kind to fail until a model is selected again.
`step_executor_configuration=...` replaces the full configuration and cannot accompany individual model selections.
Functions inherit the active settings without a decorator override.
`ModelSelection` is inert and is resolved by the active executor before installing a scope.

The active executor's optional `ConfigurableStepExecutor.resolve_model_selection()` accepts, maps, or rejects selection synchronously.
Core never unwraps a host executor to bypass its policy, and verifies the returned executor protocol.
Direct `AgentStepExecutor` supports resolution; subclasses must explicitly implement preserving resolution.
Borrowed model identity and client ownership remain with the application; externally supplied agents own `execute` model selection while `decision_model` remains independently configurable.
Selection alone preserves scoped usage meters, capabilities, oversight, lifecycle, task references, and the call handler.
Explicit executor replacement, cleared hooks, and new runs retain their documented effects and are not prohibited.

Hosts may use a Pydantic AI wrapping request capability for admission and one accounting record per visible request.
Keep selected model identity separate from the reported model name, and unknown usage separate from zero.
Pydantic AI can normalize absent usage to zero; the executable example conservatively treats an all-zero total as unknown.
Transport errors and cancellations without exposed usage do not invent a successful observation.
A host accumulator of 120 after two admitted requests against a threshold of 100 refuses the third sequential request.
Concurrent or already admitted requests may overrun that threshold; Core supplies neither reservations nor a strict cost cap.
`UsageMeter` remains a separate completed-agent-run aggregate: do not add it to request observations as another charge.
Coding-agent usage follows Pydantic AI's token convention: Claude Code input totals include uncached input, cache reads, and cache writes; the separate cache counters are subsets of that total.
These observations cover capability-visible requests, not every provider SDK retry, HTTP attempt, or coding-agent internal request.

### Coroutine ownership

DSL tool expressions await an awaitable result once, including from synchronous Natural functions.
Top-level await compilation creates a Core-owned wrapper; rejecting that unstarted wrapper closes it without executing its body.
A returned Python coroutine may be borrowed, retained by a helper, or nested inside an application value; rejection and normalization failure do not authorize closing it.
Application helpers remain responsible for unstarted coroutine temporaries they create and discard before an expression yields a value.
Core does not recursively dispose application containers or silently await ordinary synchronous Python returns.
