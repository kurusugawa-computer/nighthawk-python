---
paths:
  - "tests/**"
---

# Testing (pytest)

## Directory roles

| Directory | Purpose | Determinism | Cost |
|---|---|---|---|
| `tests/` | Pytest suite: unit tests (ScriptedExecutor) and integration tests (real LLM) | Unit: deterministic. Integration: non-deterministic but single-run. | Unit: free. Integration: API calls. |
| `src/nighthawk/testing.py` | Test utility API for deterministic Natural-function tests (`ScriptedExecutor`, `CallbackExecutor`, and response factories). | Deterministic. | Free. |

## Workflow

### Scope boundary (pytest vs promptfoo)

- Do not force prompt behavior validation into pytest-only checks.
- When prompt rendering, system prompt text, suffix generation, or tool-exposure behavior changes, follow `.claude/rules/promptfoo.md`.

### Python code changes (tools, executor, contracts)

1. Write or update unit tests in `tests/` first.
2. Prefer helpers from `nighthawk.testing` (for example `ScriptedExecutor`, `CallbackExecutor`, `pass_response`, `return_response`) when avoiding live LLM calls.
3. Run `uv run pytest -q`.
4. If the change affects prompt rendering or tool behavior, follow `.claude/rules/promptfoo.md` and run the relevant eval subset.
