---
paths:
  - "tests/**"
---

# Testing (pytest)

- Prefer deterministic pytest coverage by default. Use helpers from `nighthawk.testing` before reaching for live LLM calls.
- Use `tests/execution/stub_executor.py` only for envelope and runtime parser checks; prefer `nighthawk.testing` for normal Natural-function tests.
- Keep live-LLM tests in `tests/integration/` and behind the documented environment gates.
- For Python behavior changes, add or update pytest coverage in the same change and run `uv run pytest -q`.
- If a change affects public API or README examples, confirm `tests/public/`. If it affects docs examples or anchors, confirm `tests/docs/`.
- If a change affects prompt rendering, system prompt text, suffix generation, or tool exposure behavior, follow `.claude/rules/promptfoo.md`.
