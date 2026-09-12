# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `StepEnvironment` separates Python lexical bindings, inherited working state, scoped task references, and tracked step assignments, with source-qualified lookup and progressing discovery.
- Replaceable `ContextBuilder` presentation and shared static value inspection preserve application helper-name collisions and complete discovery continuations, including pages nested in ordinary containers.
- Common operation admission, awaited inspection, serialized execution, and completion records cover evaluation, assignment, return-expression resolution, and native tools. Failed tool argument admission retains its identity and original exception.
- Lifecycle start and operation notifications support asynchronous handlers alongside terminal delivery.
- `ExecutionReference.parent_step_execution_id` correlates joined child steps with their active parent.

### Changed

- `task_references` replaces `implicit_references`. Injection remains separate from Python module globals.
- Executors accept one `StepRequest` and return a resolved `StepCandidate`. Finalization validates all initialized declared outputs, rejects undeclared proposals, and independently validates return values. An initial `Raise` discards pending writes.
- Commit rewrites validate supplied replacements once without repeating inspection or expression effects; supplied output mappings replace the full selected mapping.
- Dotted assignment requires an ordinary Pydantic field with `validate_assignment=True`. Right-hand-side evaluation precedes live attribute traversal, and earlier effects are not rolled back on failure.
- Ordinary application and propagated validator exceptions cross model and backend boundaries unchanged. Runtime input errors and explicit Pydantic AI control signals retain their recovery paths.
- `Oversight.inspect_operation` replaces both tool-call and return-expression inspection hooks with one `OperationRequest` boundary.
- Output contracts use supported explicit annotations before initial-value inference; unresolved required annotations fail preparation rather than weakening validation.
- Lexical support is bounded to source-available functions, live defining-module globals, and existing closure cells, including declared global/nonlocal output writeback. Natural text alone does not capture an otherwise inaccessible outer variable. Parent-state inheritance covers joined child calls; deferred generated expressions and children that outlive their parent have no lifetime guarantee.
- Integrated Natural execution contracts into the specification and usage guides, with practical parent/child examples and explicit validation, inheritance, and host-delivery boundaries. The README and landing-page example now demonstrate typed output validation.

### Fixed

- Source instrumentation retains defaults, live closure cells and globals, Python unbound-name behavior, and application names that resemble generated helpers.
- Variadic output bindings validate their tuple or dictionary elements, preserving `Annotated` constraints and unpacked collection contracts.
- Python 3.14 callable descriptions recover source annotation spelling without invoking deferred evaluators. Natural decoration preserves deferred annotations, and global-output preparation obtains deferred module contracts.
- Ordered model tool batches and coding-agent callbacks preserve per-step operation order and stop after terminal operation failure.
- Native model batches also order argument validation before each corresponding body, preventing later validators from running ahead of an earlier rejection. A scoped Pydantic AI 2.37 compatibility adapter preserves its completion strategies; the dependency range is bounded to that minor series.
- Coding-agent callbacks use the request's event loop and captured scope; process and transport failures preserve their originating exceptions.
- Interrupted asynchronous notifications and synchronous bridge shutdown share one cleanup deadline, including handlers that suppress cancellation.

### Removed

- `scope(implicit_references=...)` and `get_implicit_references()`; use scoped task references and the active step environment.
- `Oversight.inspect_tool_call`, `Oversight.inspect_return_expression`, and the associated `ToolCall`, `ToolCallDecision`, and `ReturnExpression` types.
- `Rewrite.return_value`; replace a return through `Rewrite.outcome`.
- The executor protocol's separate program, context, binding, and control arguments and outcome/bindings tuple result; use `StepRequest` and `StepCandidate`.

### Migration notes for hosts

These changes apply after 0.14.0; earlier release sections describe their own APIs.
The removed entry points have no compatibility aliases.

| Before | After |
| --- | --- |
| `nh.scope(implicit_references=task_name_to_value)` | `nh.scope(task_references=task_name_to_value)`; task injection no longer overrides Python globals. Omission, `UNSET`, replacement, and `Merge` keep their composition roles. |
| `nh.get_implicit_references()` | During a step, use `nh.get_step_context().environment.task_name_to_value`. Outside a step, retain the task mapping supplied by the host; there is no run-only replacement getter. |
| `inspect_tool_call(ToolCall)` / `inspect_return_expression(ReturnExpression)` | Install `inspect_operation(operation: OperationRequest)`, returning `Accept` or `Reject`, directly or asynchronously. Inspect `operation.kind` and the applicable expression/tool fields; ordinary helper calls through evaluation have kind `"evaluation"`. |
| `ToolCallDecision` | Use `Accept` or `Reject` for operation decisions; `StepCommitDecision` additionally permits `Rewrite`. |
| `Rewrite(return_value=value)` | `Rewrite(outcome=Return(value))`, importing `Return` from `nighthawk.runtime`. Use `Return(None)` for an explicit `None`, subject to the return contract. |
| `run_step(...)` / `run_step_async(...)` with separate boundary arguments and a tuple result | Accept keyword-only `request: StepRequest` and return `StepCandidate(outcome=..., binding_name_to_value=...)`. Import both types and resolved outcomes from `nighthawk.runtime`; read live values through `request.step_context.environment`. |
| An outer value mentioned only in Natural text | Pass it explicitly, establish an existing cell with a valid `nonlocal` declaration, or inject a task reference. |

Custom executors still pass through mandatory finalization.
Ordinary helper exceptions now propagate to Python; return a domain observation when an expected failure should be handled by the model.
See the [executor contract](docs/specification.md#14-step-executor), [environment boundaries](docs/specification.md#81-state-layers-python-locals-and-step-locals), and [host configuration guide](docs/runtime-configuration.md#oversight-in-scopes) for complete semantics.

## [0.14.0]

### Added

- Scoped `nh.lifecycle.StepLifecycle` delivers typed completion, DSL raise, failure, or interruption records after generated caller assignments. Hosts can record one terminal event and translate failures with explicit exception chaining.
- `Oversight.inspect_return_expression` approves expressions before core evaluation, await, and return validation.
- `StepDeliveryError` supports host storage adapters without claiming persistence succeeded.

### Changed

- `ExecutionReference` and `get_execution_reference` replace the abbreviated identity API. `step_execution_id` identifies each invocation; `source_location` identifies its source. StepContext, inspection, testing observations, terminal records, and spans share this identity.
- Runtime internal exceptions, including preparation and await failures, now propagate through `ExecutionError.step_failed` with the original exception as cause. Rejection is a classified failure. DSL exceptions remain distinct.
- Completion tracing follows actual caller assignments. Cancellation retains priority if terminal delivery fails. Delivery errors never retry execution or undo effects.
- Natural functions preserve original closure cells for nonlocal assignments and source locations for generated execution.

### Fixed

- Lifecycle handlers can rethrow an already-recorded host exception or interruption without replacing its original cause with a self-reference.
- Host integration guidance distinguishes one delivery attempt from ledger deduplication and explains the validation, await, and rewrite limits of name-only return approval.

### Removed

- `ExecutionRef`, `get_execution_ref`, and `step_id`, without compatibility aliases. Span attributes use `step.execution.id` and `step.source_location`.

## [0.13.0]

### Added

- `nh.UNSET`, `nh.UnsetType`, `nh.Extend`, and `nh.Merge` express inheritance and per-field scope composition.
- Caller-constructed Pydantic AI Models in `StepExecutorConfiguration.model`, retaining identity and the managed execution contract. Clients are borrowed; hosts own authentication delivery and lifetime.
- Host-installed usage meters, scoped native tools and Pydantic AI capabilities, and public scope getters.
- Resolved `Pass`, `Return`, `Break`, `Continue`, and `Raise` outcomes through `nh.oversight`, with explicit `Rewrite(return_value=None)` support.
- `nh.get_transformed_function()` for compiled-body inspection through conventional wrappers.

### Changed

- The default model is now `openai-responses:gpt-5.6-luna` (previously `openai-responses:gpt-5.4-nano`).
- Scope omission inherits; ordinary values replace and empty collections clear. Only oversight accepts `None`. Executor replacement precedes full configuration replacement.
- Run getters require a run; `get_step_context()` requires a step. Budget wrappers retain optional private meter discovery.
- Repeated identical tool declarations are idempotent. Conflicts use `NameConflictError` and `ToolNameConflictError`; invalid tools use `ToolDeclarationError`.
- `StepCommit.outcome` contains resolved Python values in variants. Inspection mappings preserve contained types and identity through shallow read-only views. Trusted hooks must not mutate references.
- One validated candidate is inspected, then supplied rewrites are validated before final assignment. Bindings-only rewrites preserve the resolved return; return expressions are never replayed. Rejection cannot roll back preceding host side effects.
- Decorated functions share real module globals; `__wrapped__` and `inspect.unwrap` follow the original function.
- Live Model configuration serialization requires explicit model-field exclusion, including nested records. External agents reject Model injection and configured model switches rather than silently changing a label.

### Removed

- Global `scope(mode=...)`, the old `get_current_usage_meter` and `get_current_step_context` names, and `ToolRegistrationError`; no deprecated aliases remain.
- `@nighthawk.tool` and the process-global tool registry. Declare tools in scopes.
- `StepCommitProposal`, old proposed/rewritten fields, and parallel `StepCommit.step_outcome`/`return_value` fields.

### Compatibility

The supported public API is `nighthawk.__all__` plus the modules listed in `docs/api.md`. Private names may change without notice. Minor releases in the 0.x series may contain the breaking changes listed here.

### Migration notes for hosts

| Before | After |
|---|---|
| `scope(mode="inherit", tools=[fn])` | `scope(tools=nh.Extend([fn]))` |
| `scope(mode="replace", implicit_references={})` | `scope(implicit_references={})` |
| `scope(implicit_references=None)` to inherit | Omit the field or use `nh.UNSET`; use `nh.Merge(mapping)` to compose |
| `get_current_usage_meter()` / `get_current_step_context()` | `get_usage_meter()` / `get_step_context()`; missing context raises |
| `ToolRegistrationError` | `ToolDeclarationError`; catch `NameConflictError` across namespaces |
| Private meter context writes | `nh.run(executor, usage_meter=meter)` or `nh.scope(usage_meter=meter)` |
| `@nh.tool` at import time | `nh.scope(tools=[fn])` or an explicit Pydantic AI `Tool` |
| `StepCommit.return_value` | Check `isinstance(commit.outcome, nh.oversight.Return)`, then read `.value` |
| Expression-based `Rewrite(step_outcome=...)` | `Rewrite(outcome=nh.oversight.Return(value=...))` |
| Bindings rewrite that implicitly recalculates return | Supply both replacement bindings and `return_value` explicitly |
| `inspect.unwrap(function)` to find compiled code | `nh.get_transformed_function(function)` |
| Credential transport via environment for managed construction | Construct authenticated Provider/Model in memory and use `StepExecutorConfiguration(model=model)` |
| Dumping configuration containing a live Model | Explicitly exclude `model`; the partial dump cannot reconstruct it |
| Changing an external agent's configured model | Replace the executor with a managed executor or a host-configured external agent |

Invalid initial results fail before oversight; use resilience retries to recover. `from_configuration(configuration=...)` retains its signature. String model settings remain serializable, and environment-configured provider strings remain supported.

## [0.12.0]

### Changed
- Clarified the boundary-first philosophy page.
- Clarified multimodal documentation and specification.
- Raised the minimum Pydantic AI version to 2.37.0 and migrated coding-agent backends to its native-tool and structured-instruction APIs.
- Raised the MCP minimum version to 2.1.1 and migrated the embedded tool server and content types to the MCP 2 API.
- Updated the Claude Agent SDK integration to 0.2.151.

## [0.11.0]

### Added
- Native Pydantic AI multimodal binding support for Natural block prompts, including `BinaryContent`, URL-based media, `UploadedFile`, and explicit dotted multimodal references.
- Multimodal tool-result transport for coding-agent backends, preserving mixed text/media order and carrying MCP image/audio content natively where supported.
- Public scope snapshot getters: `get_implicit_references`, `get_system_prompt_suffix_fragments`, and `get_user_prompt_suffix_fragments`.

### Changed
- Coding-agent backends now text-project multimodal user prompts into local file paths or URL placeholders while provider-backed executors pass `UserContent` natively.
- Tool results now retain `ToolOutcome` payloads through backend boundaries so multimodal-capable transports can render native content before falling back to JSON previews.
- Optimize Natural block runtime prompt
- Update dependencies

### Fixed
- Cleaned up staged multimodal prompt files when text projection fails before a projected request is returned.

## [0.10.0]

### Added
- Scope-level oversight hooks in `nh.scope(oversight=...)` for synchronous tool-call inspection and step-commit inspection.
- Oversight trace events and backend/wrapper coverage for accept/reject decision paths.
- Validation for `vote(min_success=...)` bounds (`>= 1` and `<= count`) with matching tests.
- Tool-boundary regression test ensuring recoverable `ToolBoundaryError` is wrapped into JSON payload.

### Changed
- Finalized committed binding validation at step boundaries, added write-root dirty tracking for dotted `nh_assign`, and aligned prompt/docs with the new validation contract.
- Introduced `ExecutionRef` (`run_id`, `scope_id`, optional `step_id`) and renamed runtime accessor to `get_execution_ref`.
- Added top-level module access for `oversight` and `resilience`, with docs and public API tests aligned.
- Consolidated tool-call oversight inspection to a single wrapper-side entry point and removed duplicate inspection guard state.
- Unified tool result handling to TypedDict-based envelope types (`ToolResult`, `ToolError`) instead of Pydantic models.
- Clarified tool-boundary policy: recoverable tool-call failures are JSON-envelope responses, host invariant violations propagate as Python exceptions.
- Updated specification and runtime configuration docs to codify envelope-vs-raise boundary semantics for tool-call failures.
- Updated API and patterns docs for `ExecutionRef`, `get_execution_ref`, and oversight/resilience namespace exposure.

### Fixed
- Backend handler now preserves wrapped tool error payloads (`{"value": ..., "error": ...}`) without dropping error details.
- Tool-call context propagation now preserves step identity in execution ref during step execution.

## [0.9.0]

### Changed

- Redesigned `nh.scope()` around `mode` semantics (`"inherit"` default, `"replace"` for explicit replacement).
- Scope prompt suffix arguments now use list-based forms: `system_prompt_suffix_fragments` and `user_prompt_suffix_fragments`.

### Removed
- Removed `step_executor_configuration_patch` from `nh.scope()`.
- Removed `StepExecutorConfigurationPatch` from the public API.

### Fixed
- Clarified and aligned docs/tests for scope merge vs replace behavior.

## [0.8.0]

### Added
- Unit tests covering prompt token-budget injection: system prompt resolves `$tool_result_max_tokens`, and custom user prompt templates can resolve the same placeholder.

### Changed
- Default step system prompt now states that tool result `value` is a preview and includes the injected max-token limit.
- User prompt template rendering now uses `Template.safe_substitute`, aligned with system prompt injection behavior and compatible with optional `$tool_result_max_tokens` placeholders.

## [0.7.0]

### Added
- `nighthawk.UsageMeter`: run-scoped, thread-safe LLM token usage accumulator. Created automatically by `nh.run()` and readable via `nh.get_current_usage_meter()`.
- `nighthawk.resilience.budget` transformer: composable token and cost budget enforcement with pre-call and post-call checks. Parameters: `tokens`, `tokens_per_call`, `cost`, `cost_per_call`, `cost_function`, `estimate_usage`.
  - `BudgetExceededError`, `BudgetLimitKind`, `CostFunction` supporting types.
  - OpenTelemetry span event `nighthawk.resilience.budget.exceeded` and `nighthawk.resilience` logger warning on budget violation.
- Resilience OpenTelemetry events for retry/timeout/circuit paths: `nighthawk.resilience.retry.attempt`, `nighthawk.resilience.retry.exhausted`, `nighthawk.resilience.timeout.triggered`, `nighthawk.resilience.circuit.opened`.

### Changed
- Project status promoted from Alpha to Beta.
- Updated one-line description.
- Removed "experimental" language from README and documentation.
- Updated PyPI keywords for improved discoverability.
- Generalized `StepContext` implicit references to value-based mappings (`implicit_reference_name_to_value`), and added additive scope injection via `nh.scope(implicit_references={...})` across nested scopes.

## [0.6.1]

### Added
- Implicit type alias discovery: callable signatures in step locals and referenced globals are now scanned for PEP 695 `TypeAliasType` references, automatically including their definitions in the prompt globals section so the LLM can resolve type names like `-> Labels`.

### Changed
- `nh_eval` and `nh_assign` provided tools are now async, directly awaiting coroutines in async contexts instead of bridging through a background thread.

## [0.6.0]

### Added
- `nighthawk.resilience` module with composable function transformers for production resilience: `retrying` (tenacity-based), `fallback`, `vote`/`plurality`, `timeout`, `circuit_breaker`/`CircuitState`/`CircuitOpenError`.
  - `tenacity>=9` as a core dependency.
- `BackendModelSettings` base class and `ClaudeCodeModelSettings` intermediate class extracting shared settings across coding agent backends.

### Changed
- Refactored backend settings hierarchy: extracted shared fields (`allowed_tool_names`, `working_directory`) into `BackendModelSettings` and Claude Code fields (`max_turns`, `permission_mode`, `setting_sources`) into `ClaudeCodeModelSettings`; renamed `claude_executable`/`codex_executable` to `executable`, `claude_max_turns` to `max_turns`.
- `nh_assign` now resolves type annotations via `get_type_hints` for plain classes and dataclasses, enabling type-mismatch retry beyond Pydantic models.
- Simplified intent hint formatting: dropped `intent: ` prefix from callable metadata comments in prompt context.
- Renamed `NIGHTHAWK_RUN_INTEGRATION_TESTS` to `NIGHTHAWK_OPENAI_INTEGRATION_TESTS` for consistency with other per-backend environment variables.
- Restructured documentation into sectioned navigation: split monolithic tutorial into focused guides (`natural-blocks`, `executors`, `runtime-configuration`, `patterns`, `verification`, `pydantic-ai-providers`); renamed `design.md` to `specification.md`; removed `practices.md`, `providers.md`, `tutorial.md`.

## [0.5.0]

### Added
- `evals/promptfoo/` evaluation harness for system prompt optimization using [promptfoo](https://www.promptfoo.dev/): custom Python provider, reusable assertions, and prompt variant A/B comparison support. See `CONTRIBUTING.md` for usage.
- `docs/philosophy.md`: design philosophy and motivation behind Nighthawk.
- `docs/practices.md`: practical patterns and binding function design guidance (extracted from tutorial).

### Changed
- Replaced `return_reference_path` with `return_expression` in step execution contract: return values are now specified as Python expressions evaluated against step locals/globals, consistent with `nh_eval`/`nh_assign` expression evaluation. This unblocks coding-agent backends (e.g. Claude Code CLI) that compute results via native tools without bridging values through `nh_assign`.
- `nh_assign` now infers binding types from initial values when no explicit annotation is provided, enabling type-mismatch retry for unannotated write bindings.
- Default `json_renderer_style` changed from `"strict"` to `"default"`, making truncation visible via `…` omission markers in prompt context and tool results.
- Merged `nh_exec` into `nh_eval`: `nh_eval` now handles expression evaluation, function calls, and in-place mutation. `nh_exec` is removed.
- Condensed system prompt: simplified tool selection guidance (single `nh_eval` tool), added execution order section, clarified tool result format.
- Condensed step execution contract (outcome prompt suffix) for reduced token usage.
- Improved `nh_assign` and `nh_eval` tool descriptions for LLM clarity.
- Restructured documentation: rewrote `index.md`, `tutorial.md`, `for-coding-agents.md`; cross-referenced specification and practice guides.
- Integration tests: replaced single `NIGHTHAWK_RUN_INTEGRATION_TESTS` gate with per-backend environment variables (`NIGHTHAWK_CODEX_INTEGRATION_TESTS`, `NIGHTHAWK_CLAUDE_SDK_INTEGRATION_TESTS`, `NIGHTHAWK_CLAUDE_CLI_INTEGRATION_TESTS`).

### Removed
- `nh_exec` tool (functionality absorbed by `nh_eval`).
- Three redundant OpenAI integration tests from `test_llm_integration.py` (covered by promptfoo evaluation harness).
- `pytest_sessionstart` credential-check hook (replaced by per-backend skip helpers).

## [0.4.0] - 2026-03-20

### Added
- `nighthawk.testing` module with test executors and convenience factories for deterministic Natural function testing without LLM API calls.

### Changed
- Rewrote testing documentation in `tutorial.md` (Section 8) and `for-coding-agents.md` (Section 8): replaced incorrect `TestModel` usage with `nighthawk.testing` utilities, added testing strategy guidance distinguishing mock tests (Python logic) from integration tests (Natural block judgment).

## [0.3.1] - 2026-03-19

### Changed
- Internal ID generation now uses `ulid.generate_ulid()` (ULID,
  26-character Crockford Base32, timestamp-sortable) in a dedicated
  module, replacing the former `generate_id` embedded in
  `runtime.scoping`.

## [0.3.0] - 2026-03-18

### Added
- `system_prompt_suffix_fragment_scope` and `user_prompt_suffix_fragment_scope` context managers for lightweight prompt fragment management without full scope overhead.
- OpenTelemetry tracer now reports `instrumenting_library_version`.

### Changed
- Simplified OpenTelemetry span hierarchy: removed implicit `nighthawk.scope` spans and `nighthawk.step_executor` spans. `nighthawk.scope` spans are now emitted only for explicit `nh.scope()` calls.
- `nighthawk.run` span no longer includes `scope.id` attribute; only `run.id` is emitted.
- Trimmed `for-coding-agents.md` for coding-agent relevance: removed deprecated `@nh.tool` references, condensed exception hierarchy, scoped overrides, and added debugging context to `StepContextLimits`.

## [0.2.0] - 2026-03-16

### Added
- Added compact step trace support for Natural block execution attempts:
  - `StepTrace`
  - `StepTraceError`
  - `nighthawk.get_step_traces()`

### Changed
- Updated CI workflow setup (`setup-uv`) in project automation.

### Fixed
- License badge reference in README.
- Documentation formatting inconsistencies.

## [0.1.0] - 2026-03-13

### Added
- Initial public release of `nighthawk-python`.
- Natural DSL execution runtime with run/scope execution context model.
- Step executor abstraction and provider integration foundation.
- Core documentation and project scaffolding.

[Unreleased]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.14.0...HEAD
[0.14.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kurusugawa-computer/nighthawk-python/tree/v0.1.0
