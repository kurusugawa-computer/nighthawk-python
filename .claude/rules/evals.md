---
paths:
  - "evals/**"
---

# Evaluations

- Comments and docstrings: follow `.claude/rules/python.md`.
- Under `evals/promptfoo/`, follow `.claude/rules/promptfoo.md`.
- Files under `evals/*/evidence/` are dated records. Exclude them from comment cleanups and do not rewrite them.
- Keep experiment implementations only for a current investigation. Record completed decisions and source revisions in evidence, then remove unused treatments, runners, and their dedicated tests.
