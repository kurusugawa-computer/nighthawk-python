---
paths:
  - "src/**/*.py"
---

# Coding standards

- Apply the workspace practices across `local < module < package < global` scope. Keep helpers local or module-private; a test import does not justify a wider interface.
- Re-export a symbol only when the package owns a distinct, documented entry point. Documenting a module member does not require a package alias.
- Keep contract modules independent of executors, rendering, transport, and application policy. Put adapter-specific behavior with its consumers.
- Validate at the boundary that owns the contract. Repeat validation only when values can change or another entry point needs its own enforcement.
- If a change expands or changes public API, update the behavior owner tests and confirm export resolution in `tests/test_public_api.py`.
- Prefer async implementations in `runtime/` and `backends/`; keep sync bridges at synchronous public entry points and preserve caller-thread execution where required.
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
