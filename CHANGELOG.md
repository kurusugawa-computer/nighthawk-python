# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kurusugawa-computer/nighthawk-python/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kurusugawa-computer/nighthawk-python/tree/v0.1.0
