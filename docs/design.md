# Nighthawk design

This document is the specification for Nighthawk.

## 0. Document scope

This document specifies:

- What counts as a Natural block (docstring and inline).
- The Natural DSL binding syntax (`<name>`, `<:name>`).
- The execution model (state layers, tools, and the final JSON contract).
- The host-facing environment API and configuration surface.

This document does not attempt to describe every compilation or implementation detail. When the current implementation diverges from this specification, we record the divergence in `Known gaps`.

## 0.1 Authority rules (D-01..D-20)

Each decision below is an authority rule for resolving contradictions.

- D-01: Code canonical.
- D-02: Remove the legacy LOCALS short summary field from this specification; any legacy placeholder in the prompt template must be tracked as a Known gap; the concept belongs only in `docs/roadmap.md`.
- D-03: Code canonical.
- D-04: Code canonical.
- D-05: Doc canonical. If the code differs, record the mismatch in Known gaps.
- D-06: Doc canonical. If tests are missing, record the need for tests in Known gaps.
- D-07: Code canonical.
- D-08: Doc canonical. Record the gap(s) in Known gaps.
- D-09: Doc canonical. Record the gap(s) in Known gaps.
- D-10: Doc canonical. Record the gap(s) in Known gaps.
- D-11: Doc canonical. Record the gap(s) in Known gaps.
- D-12: Doc canonical. Record the gap(s) in Known gaps.
- D-13: Code canonical (stub backend contract).
- D-14: Keep the current design behavior as-is (break/continue enforcement); do not add a Known gap entry.
- D-15: Doc canonical. Record the gap(s) in Known gaps.
- D-16: Doc canonical for roadmap wording and granularity.
- D-17: `ExecutionConfiguration.model` uses `provider:model` format. Default: `openai:gpt-5-nano`.
- D-18: Spec terminology uses `execution_locals` and `execution_globals` (not `context_*`).
- D-19: `nh_assign` target grammar is `name(.field)*`: multi-level dotted targets; arbitrary attribute assignment; no `<name>` target form.
- D-20: Commit model is staged `execution_locals`, with `<:name>` selecting commit to Python locals for top-level names only; dotted mutation is independent.

## 0.2 Known gaps (ledger)

When the implementation and this document diverge, we record a ledger entry here.

### 0.2.1 Operating rules

- Each gap has a stable Id: `GAP-<AREA>-<NN>`.
- Each entry includes:
  - Spec (what this document requires)
  - Current implementation (what the code does today)
  - Impact
  - Follow-up (docs-only, tests-needed, or implementation)
  - Related decisions (D-xx)

### 0.2.2 D-01..D-20 coverage map

- D-01 -> Spec updated (Section 5.2, 8.2).
- D-02 -> Known gap entry `GAP-PROMPT-01` and roadmap item (see `docs/roadmap.md`).
- D-03 -> Spec updated (Section 8.2).
- D-04 -> Spec updated (Section 8.2).
- D-05 -> Known gap entry `GAP-SENTINEL-01`.
- D-06 -> Known gap entry `GAP-TESTS-01`.
- D-07 -> Spec updated (Section 7).
- D-08 -> Known gap entry `GAP-TYPES-01`.
- D-09 -> Known gap entry `GAP-NH_ASSIGN-01`.
- D-10 -> Spec updated (Section 8.1, 8.3).
- D-11 -> Spec updated (Section 10).
- D-12 -> Known gap entry `GAP-TEMPLATING-01`.
- D-13 -> Spec updated (Section 8.5).
- D-14 -> Spec updated (Section 8.4).
- D-15 -> Known gap entry `GAP-NH_ASSIGN-01`.
- D-16 -> Roadmap only (see `docs/roadmap.md`).
- D-17 -> Spec updated (Section 3, 5.2); Known gap entry `GAP-MODEL-01`.
- D-18 -> Spec updated (Section 4, 8.1-8.3, 11); Known gap entry `GAP-TERMS-01`.
- D-19 -> Spec updated (Section 8.3); Known gap entry `GAP-NH_ASSIGN-02`.
- D-20 -> Spec updated (Section 8.1, 8.3, 7, 8.4); Known gap entry `GAP-COMMIT-01`.

### 0.2.3 Gap entries

#### GAP-PROMPT-01 (legacy empty prompt section)

- Related decisions: D-02
- Spec:
  - The execution user prompt template has no legacy empty section between the program and the full locals rendering.
- Current implementation:
  - The default execution user prompt template still includes an additional section that is always rendered as an empty string in v1.
- Impact:
  - Mostly cosmetic, but it adds protocol surface area and can confuse readers and downstream prompt tooling.
- Follow-up:
  - Implementation.

#### GAP-SENTINEL-01 (Natural sentinel strictness)

- Related decisions: D-05
- Spec:
  - For both docstring Natural blocks and inline Natural blocks, the underlying string literal must begin with the exact prefix `natural\n`.
  - No leading blank lines are allowed before the sentinel; the first character of the literal must be `n`.
  - The sentinel line is case-sensitive and must be exactly `natural` (no leading or trailing whitespace).
- Current implementation:
  - Natural sentinel detection skips leading empty lines before matching `natural`.
- Impact:
  - Code accepts inputs that this spec rejects, which makes strict parsing behavior unclear to users.
- Follow-up:
  - Implementation (tighten parsing) and tests.

#### GAP-TESTS-01 (inline parentheses equivalence tests)

- Related decisions: D-06
- Spec:
  - Parentheses do not affect whether an inline Natural block is recognized.
- Current implementation:
  - Parentheses do not affect recognition in practice.
  - There is no dedicated test that locks this behavior.
- Impact:
  - Regression risk: future refactors could accidentally make parentheses significant.
- Follow-up:
  - Tests-needed.

#### GAP-TYPES-01 (compile-time type extraction not wired end-to-end)

- Related decisions: D-08
- Spec:
  - At compile time, Nighthawk extracts type information for `<:name>` from the function source AST.
  - That extracted type information is used to validate/coerce values assigned via `nh_assign(name, ...)`.
  - If a type annotation is not present, the type is treated as `Any`.
- Current implementation:
  - `nh_assign` can validate only if explicit `type_hints` are provided to the tool.
  - There is no end-to-end pipeline that connects `<:name>` annotations to the tool.
- Impact:
  - Typed assignment behavior exists only in a partial or manual form.
- Follow-up:
  - Implementation and tests.

#### GAP-NH_ASSIGN-01 (diagnostic object and atomic failure)

- Related decisions: D-09, D-15
- Spec:
  - `nh_assign` always returns a diagnostic object describing success or failure.
  - On any evaluation or validation failure, `nh_assign` is atomic: it performs no updates.
- Current implementation:
  - `nh_assign` returns a reduced object on success.
  - Validation failures may raise exceptions instead of returning a diagnostic object.
- Impact:
  - The LLM cannot reliably handle assignment failures using structured diagnostics.
  - Error handling behavior is inconsistent across evaluation vs validation failures.
- Follow-up:
  - Implementation and tests.

#### GAP-TEMPLATING-01 (template preprocessing uses locals only)

- Related decisions: D-12
- Spec:
  - Template preprocessing evaluates templates using the caller frame locals and globals.
- Current implementation:
  - Template preprocessing evaluates templates using the caller frame locals only.
- Impact:
  - Templates cannot reliably access module-level imports and symbols unless they are present in locals.
- Follow-up:
  - Implementation and tests.

#### GAP-MODEL-01 (provider:model format and default model)

- Related decisions: D-17
- Spec:
  - `ExecutionConfiguration.model` is a `provider:model` identifier and defaults to `openai:gpt-5-nano`.
- Current implementation:
  - The library treats `ExecutionConfiguration.model` as an opaque string passed through to the underlying agent/LLM client.
  - There is no library-provided default model value; callers must provide `ExecutionConfiguration.model` when constructing configuration/environment.
- Impact:
  - Documentation implies a default that may not exist in code, and users may be unclear on whether `openai:gpt-5-nano` is a required explicit configuration value.
- Follow-up:
  - Implementation (set an explicit default) and docs alignment.

#### GAP-TERMS-01 (execution_locals/execution_globals terminology)

- Related decisions: D-18
- Spec:
  - This spec uses `execution_locals` and `execution_globals` for the LLM evaluation environment.
- Current implementation:
  - The implementation models these as `ExecutionContext.locals` and `ExecutionContext.globals` and refers to them in code as `execution_locals`/`execution_globals` in some places.
  - Earlier versions of this spec used `context_locals`/`context_globals`.
- Impact:
  - Readers may have difficulty mapping spec text to code and tool behavior.
- Follow-up:
  - Docs-only: add a short mapping table (spec term -> code attribute) or keep the current spec-only terminology and treat code naming as internal.

#### GAP-NH_ASSIGN-02 (new nh_assign target grammar)

- Related decisions: D-19
- Spec:
  - `nh_assign` uses the unified target grammar `name(.field)*` and supports multi-level dotted attribute assignment.
  - Tool targets do not use the `<name>` form.
- Current implementation:
  - Local targets must be passed as `<name>` (angle bracket form).
  - Memory updates are supported only as `memory.<field>` and only for top-level memory fields.
  - Multi-level dotted targets (for example `name.field.subfield`) are not supported.
- Impact:
  - Tool usage in this spec will not match the current implementation.
- Follow-up:
  - Implementation and tests.

#### GAP-COMMIT-01 (commit staging and dotted mutation independence)

- Related decisions: D-20
- Spec:
  - Staged updates occur in `execution_locals`.
  - `<:name>` selects which top-level names are committed from `execution_locals` into Python locals at Natural block boundaries.
  - Dotted mutation is independent of `<:name>` and may affect shared objects even when the root name is not committed.
- Current implementation:
  - The staging area is `ExecutionContext.locals`.
  - `nh_assign` writes into `ExecutionContext.locals` (or into `memory` fields).
  - Only names in the Natural block's `<:name>` binding list are returned as bindings and then assigned back into Python locals at the Natural block boundary.
  - Dotted mutation is not supported by `nh_assign` today.
- Impact:
  - Without dotted mutation support, the "independent of `<:name>`" behavior is currently theoretical.
- Follow-up:
  - Implementation (dotted targets) and tests.

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
