---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
---

# Coding standards

- Avoid premature abstraction: new abstractions must be used by code in `src/` or `tests/` in the same change. Docs examples do not count.
- Keep identifiers module-private (leading underscore) until clearly used from outside in non-test code. Export intentionally via `__all__`.
- Pydantic-first: depend on Pydantic and Pydantic AI as required (non-optional) dependencies. Use `pydantic.BaseModel` and built-in features aggressively. Do not reimplement what either library provides (validation, coercion, parsing, schema, agent/tool abstractions).
- Observability: `opentelemetry.trace` for spans at run/scope/step/tool boundaries. `logging` (logger `"nighthawk"`) for diagnostics. No `logfire` imports in `src/`; it is dev-only.
- Type aliases: PEP 695 `type` statements for new type aliases.
- No unnecessary subdirectories under `src/`. Do not add folders with only `__init__.py` + a single class without maintainer buy-in.
