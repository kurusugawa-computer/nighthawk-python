---
paths:
  - "src/**/*.py"
---

# Coding standards

- Prefer concrete code. Add a new abstraction only when the same change uses it from non-test code.
- Default to module-private names. Export via `__all__` only for stable non-test consumers.
- If a change expands or changes public API, update or confirm `tests/public/`.
- Prefer async implementations in `runtime/` and `backends/`; keep sync bridges only at compatibility boundaries.
- Reuse the existing `NighthawkError` hierarchy before adding a new exception class.
- Prefer Pydantic (`BaseModel`, `TypeAdapter`) and Pydantic AI primitives over custom validation, parsing, schema, or agent/tool plumbing.
- Use `opentelemetry.trace` spans at run/scope/step/tool boundaries and `logging.getLogger("nighthawk")` for diagnostics. Do not import `logfire` in `src/`.
- Use PEP 695 `type` statements for new type aliases.
- Ask before adding a new `src/` subpackage for a single module.
- Follow `CONTRIBUTING.md` § Docstring Guide for docstring scope and format.
