# AI Agent instructions for nighthawk-python

This file provides repository-specific guidance for coding agents and human contributors.

## NON-NEGOTIABLE REQUIREMENTS

- Do not edit files until all questions in the current chat session are resolved and explicit user permission is granted (except ExecPlans).
- When listing questions, confirmations, or proposed decisions for the user, assign a short stable Id to each item so the user can respond inline.
  - Required format: `Q-FOO-01` (questions), `C-FOO-01` (confirmations), `P-FOO-01` (proposals). For follow-ups, append a suffix like `Q-FOO-01A`.
  - Each item must be answerable on its own and must include its Id in the text.

## ExecPlans

Only create an ExecPlan when author instruction explicitly requests it. If an ExecPlan is not explicitly requested, do not use ExecPlan files, even for large or risky changes.
When an ExecPlan is explicitly requested, author it following `.agent/PLANS.md` before you touch code.
Treat the ExecPlan as both an executable specification and a transparency artifact: keep `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` updated as work proceeds.
Store ExecPlans under `.agent/execplans/` and filenames must be `YYYYMMDD-<slug>.md` (ASCII only, lowercase, hyphen-separated).

## Design principles

- Avoid premature abstraction: Do not add classes/parameters just for hypothetical reuse; match the current call graph.
- Naming: Use full words in identifiers (function names, parameter names, return names, class/attribute names, and local variable names) unless defined in the Glossary.
  - Disallowed abbreviations include: `ctx`, `cfg`, `repo`, `opts`, `ref`.
  - Prefer: `context`, `configuration`, `repository`, `options`, `reference`.
  - If existing code violates these naming rules, ask the user how to proceed before doing broad renames across a file or the codebase.
- Type aliases: Prefer PEP 695 `type` aliases when introducing new type aliases.
- When the user requests "radical" changes, prioritize extensive, global, disruptive, or thorough edits to the entire codebase and documentation over minimal fixes.
- ASCII punctuation only: Use `'` (U+0027) and `"` (U+0022). Do not use smart quotes.

## Glossary

- `Id` = Identifier
- `DSL` = Domain Specific Language

### Allowed abbreviations

- Loop indices: `i`, `j`, `k` (e.g., `for i in range(n): ...`).

## Context and orientation

### Repository layout

- `src/nighthawk/`: The library package.
- `tests/`: Pytest suite.
- `docs/`: Product/design documentation.
  - `docs/README.md`: User-facing overview.
  - `docs/design.md`: Design specification.
  - `docs/roadmap.md`: Non implemented items.
- `.agent/`: Agent artifacts.
  - `.agent/execplans/`: ExecPlans (only when explicitly requested).
  - `.agent/PLANS.md`: The ExecPlan format and requirements.
- `.devcontainer/`: Devcontainer definition (Python 3.14 base image).
- `pyproject.toml`, `uv.lock`: Project metadata and locked dependencies.

### What this repo is

- A Python 3.14+ library that embeds a small "Natural" DSL inside Python functions and executes it using an LLM (via `pydantic-ai-slim[openai]`).
- There is no CLI entry point currently.

### Safety model (MVP)

- MVP assumes Natural DSL sources and any included markdown are trusted, repository-managed assets.
- Do not wire untrusted user input into Natural blocks or template preprocessing.

## Development workflow

### Tooling

- Python: 3.14+.
- Dependency management: `uv`.
- Tests: `pytest`.
- When performing code analysis (type errors, symbol navigation, call graph understanding), prefer LSP-based tooling first.

### Common commands (run from repo root)

- Install/sync dependencies:
  - `uv sync`

- Format code:
  - `uv run ruff format .`

- Run lint checks:
  - `uv run ruff check .`

- Auto-fix lint issues:
  - `uv run ruff check --fix .`

- Run the full test suite:
  - `uv run pytest`

- Run tests quietly:
  - `uv run pytest -q`

- Enable and run integration tests (OpenAI smoke):
  - `NIGHTHAWK_RUN_INTEGRATION_TESTS=1 uv run pytest -q`

- Run python for investigating:
  - `uv run python`

If you see an `uv` warning about hardlinking (common in containers / cross-filesystem workspaces), it does not indicate test failure. If you want to suppress it:

- `export UV_LINK_MODE=copy`

### Environment variables

- `OPENAI_API_KEY`: Required for any OpenAI integration.
- `NIGHTHAWK_MODEL`: Model name override (defaults to `gpt-5.2`).
- `NIGHTHAWK_RUN_INTEGRATION_TESTS=1`: Enables integration tests (otherwise skipped).
