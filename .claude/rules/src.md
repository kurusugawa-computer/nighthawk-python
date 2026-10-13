---
paths:
  - "src/**/*.py"
---

# Coding standards

- Prefer concrete code. Add a new abstraction only when the same change uses it from non-test code.
- Default to module-private names. Export via `__all__` only for stable non-test consumers.
- If a change expands or changes public API, update the behavior owner tests and confirm export resolution in `tests/test_public_api.py`.
- Prefer async implementations in `runtime/` and `backends/`; keep sync bridges only at compatibility boundaries.
- Reuse the existing `NighthawkError` hierarchy before adding a new exception class.
- Prefer Pydantic (`BaseModel`, `TypeAdapter`) and Pydantic AI primitives over custom validation, parsing, schema, or agent/tool plumbing.
- Use `opentelemetry.trace` spans at run/scope/step/tool boundaries and `logging.getLogger("nighthawk")` for diagnostics. Do not import `logfire` in `src/`.
- Use PEP 695 `type` statements for new type aliases.
- Ask before adding a new `src/` subpackage for a single module.

## Comments and docstrings

Follow `.claude/rules/python.md`. Additionally:

- Public API means the objects rendered by `docs/api.md`. Follow `CONTRIBUTING.md` "Docstring Guide" for their format.
- Keep examples in `docs/`, not in docstrings.
- Write an internal docstring only for a fact the code does not convey; otherwise omit it.
- Write a module docstring only when it states a responsibility boundary that the module name does not convey.
