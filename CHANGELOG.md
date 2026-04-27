# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Native Pydantic AI multimodal binding support for Natural block prompts, including `BinaryContent`, URL-based media, `UploadedFile`, and explicit dotted multimodal references.
- Multimodal tool-result transport for coding-agent backends, preserving mixed text/media order and carrying MCP image/audio content natively where supported.

### Changed
- Coding-agent backends now text-project multimodal user prompts into local file paths or URL placeholders while provider-backed executors pass `UserContent` natively.
- Tool results now retain `ToolOutcome` payloads through backend boundaries so multimodal-capable transports can render native content before falling back to JSON previews.
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

[Unreleased]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.10.0...HEAD
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
