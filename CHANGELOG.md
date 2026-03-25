# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `evals/promptfoo/` evaluation harness for system prompt optimization using [promptfoo](https://www.promptfoo.dev/): custom Python provider, reusable assertions, and prompt variant A/B comparison support. See `CONTRIBUTING.md` for usage.
- `docs/philosophy.md`: design philosophy and motivation behind Nighthawk.
- `docs/practices.md`: practical patterns and binding function design guidance (extracted from tutorial).

### Changed
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

[Unreleased]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kurusugawa-computer/nighthawk-python/tree/v0.1.0
